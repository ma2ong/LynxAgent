#!/usr/bin/env python3
"""KOL 日报真实采集器：用 opencli 读 X(推特) → DeepSeek 按个股聚合 → 写 runtime/kol_digest.json。

架构：本脚本在本地跑（opencli 复用 Chrome 的 x.com 登录会话），把当日推文聚合成
digest JSON 落盘；后端 quantcore.quant.kol_rooms.get_digest() 读该文件（新鲜则用真实数据，
否则降级占位）。X 优先，雪球/微博后续可在 collect() 里加来源。

用法（本地，需 Chrome 已登录 x.com + Browser Bridge 扩展）：
    python scripts/build_kol_digest.py
可在 SEARCH_TERMS / KOL_HANDLES 里增删跟踪对象。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "runtime" / "kol_digest.json"

# ── 跟踪对象（可编辑）────────────────────────────────────────────────
# 按个股名搜索信号最高；KOL_HANDLES 填入你偏好的 A 股博主 @handle（抓其发帖）。
SEARCH_TERMS = [
    "中际旭创", "寒武纪 算力", "宁德时代", "贵州茅台", "比亚迪",
    "A股 复盘", "A股 龙头 涨停",
]
KOL_HANDLES: list[str] = []  # 例：["caifui", "analyst_hk"] —— 会额外抓 from:<handle>
SEARCH_LIMIT = 12
MAX_TWEETS = 36


def _load_env() -> None:
    """加载 lynxagent/.env 里的 DEEPSEEK 等密钥（不覆盖已有环境变量）。"""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _opencli_path() -> str:
    for p in (os.environ.get("PATH", "") + os.pathsep
              + str(Path(os.environ.get("APPDATA", "")) / "npm")).split(os.pathsep):
        cand = Path(p) / ("opencli.cmd" if os.name == "nt" else "opencli")
        if cand.exists():
            return str(cand)
    return "opencli"


def _as_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _norm(platform: str, raw: dict) -> dict:
    """把任一站点的一条原始记录归一为统一结构。
    text 优先取 text，缺则取 title（微博 search 用 title）；likes 缺则 0；url 缺则空。"""
    text = str(raw.get("text") or raw.get("title") or "").strip()
    return {
        "platform": platform,
        "author": str(raw.get("author") or "").strip(),
        "text": text[:180],
        "url": str(raw.get("url") or ""),
        "id": str(raw.get("id") or ""),
        "likes": _as_int(raw.get("likes")),
    }


def _dedup_rank(items: list[dict], max_items: int) -> list[dict]:
    """跨源去重（platform + url/id/文本哈希），丢弃过短文本，按 likes 粗排取前 max_items。"""
    seen: set = set()
    out: list[dict] = []
    for it in items:
        if len(it.get("text") or "") < 8:
            continue
        key = (it.get("platform"),
               it.get("url") or it.get("id")
               or hash((it.get("author"), (it.get("text") or "")[:40])))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    out.sort(key=lambda x: _as_int(x.get("likes")), reverse=True)
    return out[:max_items]


def _sources(idxs, items: list[dict]) -> list[dict]:
    """把 LLM 输出的条目索引还原成真实来源；platform 取每条 item 真实平台（修硬编码 X bug）。"""
    out, seen = [], set()
    for i in idxs or []:
        if not isinstance(i, int) or i < 0 or i >= len(items):
            continue
        it = items[i]
        key = it.get("url") or it.get("author")
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "platform": it.get("platform") or "未知",
            "author": it.get("author") or "",
            "url": it.get("url") or "",
            "is_placeholder": not it.get("url"),
        })
    return out


def _assemble(agg: dict, items: list[dict], resolve) -> dict | None:
    """把 LLM 解析出的 agg(dict) + 采集 items 组装成 digest；resolve 为名称→代码解析器（注入便于测试）。"""
    stocks: list[dict] = []
    for s in agg.get("stocks") or []:
        name = str(s.get("name") or "").strip()
        resolved = resolve([name])
        if not resolved:
            continue  # 解析不到真实代码的丢弃，绝不编造
        code, full = resolved[0]["symbol"], resolved[0]["name"]
        view_blocks, authors, tids = [], set(), set()
        for b in s.get("blocks") or []:
            srcs = _sources(b.get("tweets"), items)
            if not srcs:
                continue
            for src in srcs:
                authors.add(src["author"])
            tids.update(b.get("tweets") or [])
            view_blocks.append({
                "kind": str(b.get("kind") or "多空综合分析"),
                "count": len(b.get("tweets") or []),
                "handles": [f"@{a}" for a in {src['author'] for src in srcs}][:4],
                "content": str(b.get("content") or "").strip(),
                "sources": srcs,
            })
        if not view_blocks:
            continue
        stocks.append({
            "code": code, "name": full,
            "group": str(s.get("group") or "热议"),
            "tag": str(s.get("tag") or "热议"),
            "stance": str(s.get("stance") or "中性"),
            "kol_count": len(authors), "post_count": len(tids),
            "summary": str(s.get("summary") or "").strip(),
            "view_blocks": view_blocks,
        })

    if not stocks:
        return None

    other_topics = []
    for t in agg.get("other_topics") or []:
        other_topics.append({
            "title": str(t.get("title") or "").strip(),
            "tag": str(t.get("tag") or "宏观"),
            "content": str(t.get("content") or "").strip(),
            "sources": _sources(t.get("tweets"), items),
        })

    stocks.sort(key=lambda x: (x["kol_count"], x["post_count"]), reverse=True)
    attention = [{"code": s["code"], "name": s["name"],
                  "kol_count": s["kol_count"], "post_count": s["post_count"]} for s in stocks[:6]]
    all_authors = {src["author"] for s in stocks for b in s["view_blocks"] for src in b["sources"]}
    platforms = sorted({src["platform"] for s in stocks for b in s["view_blocks"] for src in b["sources"]})
    hottest = attention[0] if attention else None
    return {
        "is_mock": False,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": datetime.now().strftime("%Y/%m/%d %H:%M"),
        "stats": {"kol_total": len(all_authors), "post_total": len(items), "hours": 24},
        "hottest": hottest,
        "attention_rank": attention,
        "stocks": stocks,
        "other_topics": other_topics,
        "sources_platform": platforms,
    }


def _search(term: str, limit: int) -> list[dict]:
    """opencli twitter search → list[tweet]。写文件再读，避免 stdin 管道破坏 JSON。"""
    tmp = ROOT / "runtime" / "_kol_fetch.json"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            subprocess.run(
                [_opencli_path(), "twitter", "search", term, "--filter", "live",
                 "--limit", str(limit), "-f", "json"],
                stdout=f, stderr=subprocess.DEVNULL, timeout=60, check=False,
            )
        data = json.loads(tmp.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as exc:
        print(f"  [warn] search '{term}' failed: {exc}", flush=True)
        return []
    finally:
        tmp.unlink(missing_ok=True)


def collect() -> list[dict]:
    """采集并按 id 去重，截断正文，取最近 MAX_TWEETS 条。"""
    seen: dict[str, dict] = {}
    queries = list(SEARCH_TERMS) + [f"from:{h}" for h in KOL_HANDLES]
    for q in queries:
        print(f"  搜索 {q} …", flush=True)
        for t in _search(q, SEARCH_LIMIT):
            tid = str(t.get("id") or "")
            if not tid or tid in seen:
                continue
            text = str(t.get("text") or "").strip()
            if len(text) < 8:
                continue
            seen[tid] = {
                "author": str(t.get("author") or ""),
                "text": text[:180],
                "url": str(t.get("url") or ""),
                "likes": t.get("likes") or 0,
            }
    tweets = list(seen.values())
    # 按点赞粗排，取前 MAX_TWEETS（高互动信号更强）
    tweets.sort(key=lambda x: int(x.get("likes") or 0), reverse=True)
    return tweets[:MAX_TWEETS]


_AGG_SYS = "你是严谨的 A 股投研编辑，只基于给定推文，不编造个股，不夸大。只输出合法 JSON。"


def _agg_prompt(tweets: list[dict]) -> str:
    lines = [f"[{i}] @{t['author']}: {t['text']}" for i, t in enumerate(tweets)]
    return (
        "下面是从推特(X)采集的中文财经推文（已编号）。请：\n"
        "1. 只保留与【A股个股】观点相关的，剔除加密货币/美股/广告/纯转发/无观点内容。\n"
        "2. 按个股聚合：每只被讨论的 A 股个股给出 综述 + 观点分类，每条引用支持它的推文编号。\n"
        "3. 给每只个股判断 stance(看多/看空/分歧/中性) 与 tag(热议/分歧/短线)。\n"
        "4. 非个股的宏观/赛道讨论归入 other_topics。\n"
        "只输出 JSON（务必完整闭合，控制长度）：{\n"
        '  "stocks":[{"name":"股票中文全称","group":"多头共识|多空对垒|短线情绪",'
        '"tag":"...","stance":"...","summary":"≤40字",'
        '"blocks":[{"kind":"多空综合分析|买入逻辑|卖出 / 风险逻辑|数据 / 客观观察",'
        '"content":"≤30字综合观点","tweets":[编号,...]}]}],\n'
        '  "other_topics":[{"title":"...","tag":"宏观|赛道|情绪","content":"≤30字","tweets":[编号]}]\n'
        "}\n"
        "约束：最多 6 只个股，每只最多 2 个 blocks，other_topics 最多 3 条。"
        "summary≤40字、content≤30字。name 必须是真实存在的 A 股公司中文全称；找不到明确个股的不要编造。\n\n"
        "推文：\n" + "\n".join(lines)
    )


def aggregate(items: list[dict]) -> dict | None:
    """调 DeepSeek 聚合，再把个股名解析成代码、把索引还原成真实来源。"""
    from quantcore.quant import llm
    from quantcore.quant.serenity_resolve import resolve_beneficiaries

    if not llm.available():
        print("  [error] LLM 不可用（缺 DEEPSEEK_API_KEY）", flush=True)
        return None
    sys_json = _AGG_SYS + "\n你必须只输出合法 JSON，不要任何解释或 markdown 代码块标记。"
    raw = llm.chat(_agg_prompt(items), sys_json, deep=True, max_tokens=4000)
    agg = llm._extract_json(raw) if raw else None
    if not isinstance(agg, dict):
        print(f"  [error] LLM 聚合返回非 JSON（raw {len(raw)} 字）: {raw[:160]!r}", flush=True)
        return None
    digest = _assemble(agg, items, resolve_beneficiaries)
    if not digest:
        print("  [error] 聚合后无可用个股（信号不足）", flush=True)
    return digest


def main() -> int:
    _load_env()
    sys.path.insert(0, str(ROOT))
    print("采集推特 KOL 内容…", flush=True)
    tweets = collect()
    print(f"采集到 {len(tweets)} 条去重推文", flush=True)
    if len(tweets) < 5:
        print("推文太少，放弃生成（保留上次/占位）。", flush=True)
        return 1
    print("DeepSeek 聚合中…", flush=True)
    digest = aggregate(tweets)
    if not digest:
        return 1
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 已写入 {OUT_PATH}：{len(digest['stocks'])} 只个股 / {len(digest['other_topics'])} 条热议", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
