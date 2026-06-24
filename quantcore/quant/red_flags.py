"""红旗快查（子项目 B）：规则（硬数据）+ LLM（软信号）混合，列出个股风险红旗及严重度。

硬规则（纯计算，reachable 数据）：
  - 股权质押：全市场帧按代码筛，质押比例 >50% 高、>30% 中。
  - 高管/股东减持：全市场帧近 90 天且原因含"减持"，命中给中/高。
  - 已大涨：本地 kline 近 60 交易日累计涨幅 >100% 高、>60% 中。
  - 估值：本地最新价 ÷ 最新 EPS 估算 PE，EPS<=0(亏损) 高、PE>100 中。
软信号（1 次 LLM）：喂财务摘要 + 名称行业 → 单一大客户依赖 / 题材透支 / 财务异常等定性提示。

任一数据源失败仅跳过该项（隔离），不抛异常。无 LLM 仅跳过软信号层，硬规则仍出。
"""
from __future__ import annotations

from datetime import date as _date, timedelta
from typing import Dict, List, Optional

from . import llm
from ._cache import cached, peek
from .data import load_local_kline
from .fundamentals import fundamentals as fetch_fundamentals

_PLEDGE_TTL = 6 * 3600
_HOLDCHANGE_TTL = 18 * 3600  # 减持帧每日收盘后预热一次即可


def _num(v) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("%", "")
    if s in ("", "-", "--", "nan", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _pick_col(df, *keys) -> Optional[str]:
    """按关键词模糊匹配列名（akshare 各版本字段名不稳定）。"""
    for k in keys:
        for col in df.columns:
            if k in str(col):
                return col
    return None


def _name_industry(symbol: str) -> Dict[str, str]:
    try:
        from .local_store import get_local_store
        row = get_local_store()._conn().execute(
            "SELECT name, industry FROM stock_meta WHERE symbol=?", (symbol,)
        ).fetchone()
        if row:
            return {"name": str(row[0] or "").strip(), "industry": str(row[1] or "").strip()}
    except Exception:
        pass
    return {"name": "", "industry": ""}


# ---- 全市场帧（缓存） ----------------------------------------------------

def _recent_fridays(n: int = 8) -> List[str]:
    """最近 n 个周五的 YYYYMMDD（质押数据按周更新，逐个尝试取第一个有数据的）。"""
    today = _date.today()
    fri = today - timedelta(days=(today.weekday() - 4) % 7)
    return [(fri - timedelta(weeks=i)).strftime("%Y%m%d") for i in range(n)]


def _pledge_key() -> str:
    return f"redflag:pledge:{_date.today().isoformat()}"


def _holdchange_key() -> str:
    return f"redflag:holdchange:{_date.today().isoformat()}"


def _pledge_compute():
    """全市场股权质押比例帧。逐个近周五尝试，返回首个非空 DataFrame。实测约 3 秒，可在线拉。"""
    import akshare as ak
    for ymd in _recent_fridays():
        try:
            df = ak.stock_gpzy_pledge_ratio_em(date=ymd)
        except Exception:
            continue
        if df is not None and not df.empty:
            return df
    return None


def _holdchange_compute():
    """全市场高管/股东持股变动帧（重，akshare 分页 ~5 分钟）。仅在预热任务里跑。"""
    import akshare as ak
    try:
        df = ak.stock_hold_management_detail_em()
    except Exception:
        return None
    return df if df is not None and not df.empty else None


def _pledge_frame():
    """质押帧：在线请求即可拉取（快），按日缓存。"""
    return cached(_pledge_key(), _PLEDGE_TTL, _pledge_compute)


def _holdchange_frame():
    """减持帧：只读预热缓存，绝不在线触发 5 分钟全市场拉取；未预热则 None（跳过减持项）。"""
    return peek(_holdchange_key(), _HOLDCHANGE_TTL)


def warm_red_flags() -> None:
    """收盘后预热：拉全市场质押 + 减持帧入缓存，使在线红旗快查无需等待重计算。"""
    cached(_pledge_key(), _PLEDGE_TTL, _pledge_compute)
    cached(_holdchange_key(), _HOLDCHANGE_TTL, _holdchange_compute)


# ---- 硬规则 --------------------------------------------------------------

def _flag_pledge(symbol: str) -> List[Dict[str, object]]:
    try:
        df = _pledge_frame()
        if df is None or df.empty:
            return []
        code_col = _pick_col(df, "股票代码", "代码")
        ratio_col = _pick_col(df, "质押比例", "质押")
        if not code_col or not ratio_col:
            return []
        hit = df[df[code_col].astype(str).str.zfill(6) == symbol]
        if hit.empty:
            return []
        ratio = _num(hit.iloc[0][ratio_col])
        if ratio is None:
            return []
        if ratio > 50:
            return [{"type": "股权质押", "severity": "高", "detail": f"质押比例 {ratio:.1f}%，偏高", "source": "rule"}]
        if ratio > 30:
            return [{"type": "股权质押", "severity": "中", "detail": f"质押比例 {ratio:.1f}%", "source": "rule"}]
    except Exception:
        return []
    return []


def _flag_holdchange(symbol: str) -> List[Dict[str, object]]:
    try:
        df = _holdchange_frame()
        if df is None or df.empty:
            return []
        code_col = _pick_col(df, "代码")
        reason_col = _pick_col(df, "变动原因", "原因")
        date_col = _pick_col(df, "变动截止日期", "变动日期", "公告日", "日期")
        if not code_col:
            return []
        hit = df[df[code_col].astype(str).str.zfill(6) == symbol]
        if hit.empty:
            return []
        if reason_col is not None:
            hit = hit[hit[reason_col].astype(str).str.contains("减持", na=False)]
        if hit.empty:
            return []
        # 近 90 天过滤（日期列可解析时）
        if date_col is not None:
            try:
                import pandas as pd
                dts = pd.to_datetime(hit[date_col], errors="coerce")
                cutoff = pd.Timestamp(_date.today() - timedelta(days=90))
                recent = hit[dts >= cutoff]
                if not recent.empty:
                    hit = recent
            except Exception:
                pass
        count = len(hit)
        sev = "高" if count >= 3 else "中"
        return [{"type": "股东减持", "severity": sev, "detail": f"近期 {count} 笔减持记录", "source": "rule"}]
    except Exception:
        return []
    return []


def _flag_surge(symbol: str) -> List[Dict[str, object]]:
    try:
        data = load_local_kline(symbol, days=120)
        if data is None or data.empty:
            return []
        close = data["close"]
        if len(close) < 61:
            return []
        chg = (float(close.iloc[-1]) / float(close.iloc[-61]) - 1) * 100
        if chg > 100:
            return [{"type": "短期已大涨", "severity": "高", "detail": f"近 60 日累计涨幅 {chg:.0f}%，透支风险", "source": "rule"}]
        if chg > 60:
            return [{"type": "短期已大涨", "severity": "中", "detail": f"近 60 日累计涨幅 {chg:.0f}%", "source": "rule"}]
    except Exception:
        return []
    return []


def _flag_valuation(symbol: str, fund: Dict) -> List[Dict[str, object]]:
    try:
        eps = fund.get("eps")
        data = load_local_kline(symbol, days=5)
        last = float(data["close"].iloc[-1]) if data is not None and not data.empty else None
        if eps is None or last is None:
            return []
        if eps <= 0:
            return [{"type": "估值/盈利", "severity": "高", "detail": f"每股收益 {eps}，公司亏损", "source": "rule"}]
        pe = last / eps if eps else None
        if pe is not None and pe > 100:
            return [{"type": "估值偏高", "severity": "中", "detail": f"按最新价/EPS 估算 PE≈{pe:.0f}，估值偏高", "source": "rule"}]
    except Exception:
        return []
    return []


# ---- 软信号（LLM） -------------------------------------------------------

def _flag_soft(symbol: str, meta: Dict[str, str], fund: Dict) -> List[Dict[str, object]]:
    if not llm.available():
        return []
    prompt = (
        f"评估 A 股【{meta.get('name') or symbol}（{symbol}）】是否存在难以从硬指标看出的潜在风险红旗。\n"
        f"行业：{meta.get('industry') or '未知'}；"
        f"营收同比 {fund.get('revenue_yoy')}%，净利同比 {fund.get('net_profit_yoy')}%，"
        f"毛利率 {fund.get('gross_margin')}%，净利率 {fund.get('net_margin')}%，ROE {fund.get('roe')}%，"
        f"经营现金流 {fund.get('operating_cashflow')}。\n"
        "只列确有依据的软风险（如：单一大客户依赖、题材透支、商誉减值隐患、现金流与利润背离、财务异常等），"
        "没有就返回空数组。\n"
        '只输出 JSON：{"flags":[{"type":"题材透支","detail":"不超过30字"}, ...]}，最多 4 条，不编造。'
    )
    result = llm.chat_json(prompt, system="你是审慎的 A 股风控分析师，只指出有依据的软风险，不臆测。")
    raw = (result or {}).get("flags") if isinstance(result, dict) else None
    if not raw:
        return []
    out = []
    for item in raw[:4]:
        if not isinstance(item, dict):
            continue
        detail = str(item.get("detail") or "").strip()[:60]
        if not detail:
            continue
        out.append({
            "type": str(item.get("type") or "软风险").strip(),
            "severity": "中",
            "detail": detail,
            "source": "llm",
        })
    return out


_SEV_RANK = {"高": 3, "中": 2, "低": 1}


def red_flag_scan(symbol: str) -> Dict[str, object]:
    """硬规则 + 软信号混合扫描。任一数据源失败隔离，不抛栈。"""
    symbol = str(symbol).strip().zfill(6) if str(symbol).strip().isdigit() else str(symbol).strip()
    meta = _name_industry(symbol)
    try:
        fund = fetch_fundamentals(symbol) or {}
    except Exception:
        fund = {}

    flags: List[Dict[str, object]] = []
    flags += _flag_pledge(symbol)
    flags += _flag_holdchange(symbol)
    flags += _flag_surge(symbol)
    flags += _flag_valuation(symbol, fund)
    flags += _flag_soft(symbol, meta, fund)

    flags.sort(key=lambda f: _SEV_RANK.get(f.get("severity"), 0), reverse=True)

    if not flags:
        risk_level = "低"
    else:
        top = max(_SEV_RANK.get(f.get("severity"), 0) for f in flags)
        risk_level = {3: "高", 2: "中", 1: "低"}.get(top, "低")

    return {
        "symbol": symbol,
        "name": meta.get("name") or symbol,
        "flags": flags,
        "risk_level": risk_level,
    }
