"""orchestrator：多 Agent 选股流水线串联器。

流程（终端日志同款分步）：
  feedback_curator (读历史反向优汰规则)
    -> theme_agent (新闻->热点主题, 线①)
    -> 多路选股汇总: strategy(线②) + money_follow + market + contrarian + retail_sentiment
    -> 同股多来源去重(合并 sources)
    -> critic (LLM/规则 打分0-10 + 拒绝理由)
    -> 落盘 pipeline_runs/<ts>/steps/{feedback_curator,stock_pick,critic}.json + picks.json

无 LLM 密钥时各 stage 自动降级为纯规则，端到端仍跑通。

待合并到 app/routers/quant.py 的路由片段（主会话统一合并，本包不写 HTTP）：

    from quantcore.quant.pipeline import run_pipeline, list_runs, get_run, run_t5_review

    class PipelineRunRequest(BaseModel):
        universe: Optional[List[str]] = None
        max_candidates: int = Field(40, ge=1, le=200)

    @router.post("/pipeline/run")
    async def quant_pipeline_run(req: PipelineRunRequest):
        return await asyncio.to_thread(run_pipeline, req.universe, req.max_candidates, True)

    @router.get("/pipeline/runs")
    async def quant_pipeline_runs():
        return {"runs": list_runs()}

    @router.get("/pipeline/runs/{run_id}")
    async def quant_pipeline_run_detail(run_id: str):
        return get_run(run_id)

    @router.post("/pipeline/t5-review")
    async def quant_pipeline_t5(run_id: Optional[str] = None):
        from quantcore.quant.pipeline.orchestrator import RUNS_DIR
        import os
        run_dir = os.path.join(RUNS_DIR, run_id) if run_id else None
        return await asyncio.to_thread(run_t5_review, run_dir, None)
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime
from typing import Dict, List, Optional

from ..factors import compute_factor_scores
from ..screening import exclusion_reason
from .critic import run_critic
from .feedback_curator import run_feedback_curator
from .sources import SOURCE_PICKERS, candidate_universe, get_ohlcv
from .strategy_pick import run_strategy_pick
from .theme_agent import run_theme_agent

_THEME_TIMEOUT = 10  # akshare 新闻接口最多等 10 秒，超时跳过


def _run_theme_agent_safe() -> Dict[str, object]:
    """带超时的 theme_agent 调用：网络超时时返回空主题而不是挂死。"""
    ex = ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(run_theme_agent)
    try:
        return fut.result(timeout=_THEME_TIMEOUT)
    except FuturesTimeout:
        return {"stage": "theme_agent", "llm_used": False, "themes": [], "note": "新闻接口超时，已跳过题材分析"}
    except Exception:
        return {"stage": "theme_agent", "llm_used": False, "themes": [], "note": "新闻接口异常，已跳过题材分析"}
    finally:
        ex.shutdown(wait=False)  # 不等挂死的网络线程，让其作为 daemon 线程在后台消亡

RUNS_DIR = os.path.join("runtime", "pipeline_runs")


def _last_rsi(df) -> float:
    try:
        from ..factors import enrich_indicators

        s = enrich_indicators(df)["rsi14"].dropna()
        return float(s.iloc[-1]) if not s.empty else 50.0
    except Exception:
        return 50.0


def _gather_candidates(symbols: List[str]) -> Dict[str, Dict[str, object]]:
    """对每只跑全部选股线，命中即收集；同股多来源合并 sources。"""
    merged: Dict[str, Dict[str, object]] = {}
    for symbol in symbols:
        df = get_ohlcv(symbol, days=540)
        if df is None or df.empty or len(df) < 60:
            continue
        sources: List[str] = []
        reasons: List[str] = []

        strat = run_strategy_pick(symbol, df)
        if strat:
            sources.append("strategy")
            reasons.append(strat["reason"])

        for name, picker in SOURCE_PICKERS.items():
            try:
                hit = picker(symbol, df)
            except Exception:
                hit = None
            if hit:
                sources.append(hit["source"])
                reasons.append(hit["reason"])

        if not sources:
            continue

        factors = compute_factor_scores(df)
        merged[symbol] = {
            "symbol": symbol,
            "name": "",
            "sources": list(dict.fromkeys(sources)),
            "source_reasons": reasons,
            "reason": " | ".join(reasons[:4]),
            "factors": factors,
            "snapshot": {"rsi": round(_last_rsi(df), 1)},
        }
    return merged


def _persist(run_dir: str, steps: Dict[str, object], result: Dict[str, object]) -> None:
    steps_dir = os.path.join(run_dir, "steps")
    os.makedirs(steps_dir, exist_ok=True)
    for name, payload in steps.items():
        with open(os.path.join(steps_dir, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    with open(os.path.join(run_dir, "picks.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)


def run_pipeline(
    universe: Optional[List[str]] = None,
    max_candidates: int = 40,
    persist: bool = True,
) -> Dict[str, object]:
    """端到端跑流水线。universe 可传小列表用于测试；None 时从本地股票池取。"""
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    today = datetime.now().strftime("%Y-%m-%d")

    # 1) feedback_curator
    feedback = run_feedback_curator()

    # 2) theme_agent（线①热点主题），带网络超时保护
    theme = _run_theme_agent_safe()
    theme_keywords = [kw for t in theme.get("themes", []) for kw in t.get("keywords", [])]

    # 3) 准备 universe + 第一层排除（ST/停牌/仙股/无量）
    if universe:
        pool = [{"symbol": str(s).zfill(6), "name": ""} for s in universe]
    else:
        pool = candidate_universe(limit=max(max_candidates * 5, 100))

    symbols, excluded = [], 0
    for meta in pool:
        sym = meta["symbol"]
        df = get_ohlcv(sym, days=60)
        if df is None or df.empty:
            excluded += 1
            continue
        last = df.iloc[-1]
        price = float(last.get("close", 0) or 0)
        amount = float(last.get("amount", 0) or 0)
        # 名称未知时只用价格/量做排除（ST 需名称，universe 无名称时跳过该项）
        if exclusion_reason(meta.get("name", ""), price, amount):
            excluded += 1
            continue
        symbols.append(sym)

    # 4) 多路选股 + 去重
    merged = _gather_candidates(symbols)
    # 回填名称
    name_map = {m["symbol"]: m.get("name", "") for m in pool}
    candidates = []
    for sym, cand in merged.items():
        cand["name"] = name_map.get(sym, "") or sym
        candidates.append(cand)
    candidates = candidates[:max_candidates]

    stock_pick = {
        "stage": "stock_pick",
        "universe_size": len(pool),
        "excluded": excluded,
        "scanned": len(symbols),
        "theme_keywords": theme_keywords,
        "candidates": candidates,
    }

    # 5) critic 打分 + 拒绝
    critic_out = run_critic(candidates, feedback.get("avoid_tags"))
    # 给 kept 标注 pick_date，供后续 T+5 复盘
    for item in critic_out["kept"]:
        item["pick_date"] = today

    result = {
        "run_id": run_id,
        "date": today,
        "llm_used": {"theme": theme.get("llm_used"), "critic": critic_out.get("llm_used")},
        "themes": theme.get("themes", []),
        "feedback_notes": feedback.get("notes", []),
        "universe_size": len(pool),
        "candidates": len(candidates),
        "kept": critic_out["kept"],
        "rejected": critic_out["rejected"],
    }

    if persist:
        run_dir = os.path.join(RUNS_DIR, run_id)
        _persist(run_dir, {"feedback_curator": feedback, "stock_pick": stock_pick, "critic": critic_out}, result)
        result["run_dir"] = run_dir

    return result


def list_runs() -> List[Dict[str, object]]:
    """列出历史 run（按时间倒序）。"""
    if not os.path.isdir(RUNS_DIR):
        return []
    out = []
    for name in sorted(os.listdir(RUNS_DIR), reverse=True):
        picks = os.path.join(RUNS_DIR, name, "picks.json")
        if not os.path.exists(picks):
            continue
        try:
            with open(picks, "r", encoding="utf-8") as f:
                data = json.load(f)
            out.append({
                "run_id": name,
                "date": data.get("date"),
                "candidates": data.get("candidates"),
                "kept": len(data.get("kept", [])),
                "rejected": len(data.get("rejected", [])),
            })
        except Exception:
            continue
    return out


def get_run(run_id: str) -> Dict[str, object]:
    """读取某次 run 的完整 picks.json。"""
    picks = os.path.join(RUNS_DIR, run_id, "picks.json")
    if not os.path.exists(picks):
        return {"error": "run not found", "run_id": run_id}
    with open(picks, "r", encoding="utf-8") as f:
        return json.load(f)


def quick_critic_batch(
    symbols: List[str],
    names: Optional[Dict[str, str]] = None,
) -> Dict[str, object]:
    """对给定股票列表做纯规则 critic 打分（不发网络请求，只用本地 K 线）。

    供 smart_pool / pattern_pool 结果的 AI 评审富集，调用方传入 symbol 列表，
    返回 {symbol: {score, keep, reject_reason}} 供前端添加列。
    本地无 K 线的股票直接跳过（不返回）。
    """
    from .critic import attach_factors, run_critic

    candidates = []
    for sym in symbols[:100]:
        sym = str(sym).zfill(6)
        df = get_ohlcv(sym, days=200)
        if df is None or df.empty or len(df) < 30:
            continue
        factors = attach_factors(sym, df)
        snapshot: Dict[str, float] = {}
        try:
            from ..factors import enrich_indicators
            e = enrich_indicators(df)
            if "rsi14" in e:
                rsi_s = e["rsi14"].dropna()
                if not rsi_s.empty:
                    snapshot["rsi"] = float(rsi_s.iloc[-1])
        except Exception:
            pass
        candidates.append({
            "symbol": sym,
            "name": (names or {}).get(sym, ""),
            "sources": ["quick_eval"],
            "factors": factors,
            "snapshot": snapshot,
            "reason": "",
            "source_reasons": [],
        })

    if not candidates:
        return {"scores": {}, "total": 0}

    critic_out = run_critic(candidates)
    scores: Dict[str, object] = {}
    for item in critic_out.get("kept", []) + critic_out.get("rejected", []):
        scores[item["symbol"]] = {
            "score": item["score"],
            "keep": item["keep"],
            "reject_reason": item.get("reject_reason"),
        }
    return {"scores": scores, "total": len(candidates)}
