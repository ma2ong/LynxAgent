"""财经日历：财报披露 / 限售解禁 / 新股申购 三源合并为按日期排序的时间线。

宏观源（baidu）需 cookie 不稳，本期不做。各源独立 try/except，单源失败隔离，全失败才 empty。
- 财报：stock_yysj_em(symbol='沪深A股', date=<报告期>) 取「首次预约时间」
- 解禁：stock_restricted_release_detail_em(start_date, end_date) 直接落在窗口内
- 新股：stock_xgsglb_em(symbol='全部股票') 取「申购日期」
"""
from __future__ import annotations

from datetime import date as _date, timedelta
from typing import Dict, List

from ._cache import cached

_TTL = 6 * 3600


def _ymd(d: _date) -> str:
    return d.strftime("%Y%m%d")


def _next_report_period() -> str:
    """下一个报告期 YYYYMMDD（季度末，>= 今天），供财报预约披露接口。"""
    t = _date.today()
    cands = [f"{t.year}0331", f"{t.year}0630", f"{t.year}0930", f"{t.year}1231", f"{t.year + 1}0331"]
    ymd = t.strftime("%Y%m%d")
    return next((c for c in cands if c >= ymd), cands[-1])


def _in_window(d: str, today: str, horizon: str) -> bool:
    return bool(d) and today <= d <= horizon


def _fetch_earnings(days: int) -> List[dict]:
    import akshare as ak
    df = ak.stock_yysj_em(symbol="沪深A股", date=_next_report_period())
    if df is None or df.empty:
        return []
    today = _date.today().isoformat()
    horizon = (_date.today() + timedelta(days=days)).isoformat()
    out = []
    for _, r in df.iterrows():
        d = str(r.get("首次预约时间", ""))[:10]
        if _in_window(d, today, horizon):
            out.append({"date": d, "type": "earnings", "title": f"{r.get('股票简称', '')} 财报披露"})
    return out


def _fetch_unlock(days: int) -> List[dict]:
    import akshare as ak
    today = _date.today()
    df = ak.stock_restricted_release_detail_em(
        start_date=_ymd(today), end_date=_ymd(today + timedelta(days=days)))
    if df is None or df.empty:
        return []
    out = []
    for _, r in df.iterrows():
        d = str(r.get("解禁时间", ""))[:10]
        if not d:
            continue
        try:
            mv = f" {round(float(r.get('实际解禁市值')) / 1e8, 1)}亿"
        except (TypeError, ValueError):
            mv = ""
        out.append({"date": d, "type": "unlock", "title": f"{r.get('股票简称', '')} 解禁{mv}"})
    return out


def _fetch_ipo(days: int) -> List[dict]:
    import akshare as ak
    df = ak.stock_xgsglb_em(symbol="全部股票")
    if df is None or df.empty:
        return []
    today = _date.today().isoformat()
    horizon = (_date.today() + timedelta(days=days)).isoformat()
    out = []
    for _, r in df.iterrows():
        d = str(r.get("申购日期", ""))[:10]
        if _in_window(d, today, horizon):
            out.append({"date": d, "type": "ipo", "title": f"{r.get('股票简称', '')} 新股申购"})
    return out


_FETCHERS = {
    "earnings": lambda days: _fetch_earnings(days),
    "unlock": lambda days: _fetch_unlock(days),
    "ipo": lambda days: _fetch_ipo(days),
}


def financial_calendar(types: List[str], days: int = 14) -> Dict[str, object]:
    def _compute():
        events: List[dict] = []
        for t in types:
            fn = _FETCHERS.get(t)
            if not fn:
                continue
            try:
                events.extend(fn(days) or [])
            except Exception:
                continue  # 单源失败隔离
        events.sort(key=lambda e: (e["date"], e["type"]))
        return {"empty": len(events) == 0, "events": events,
                "message": "" if events else "日历数据暂不可用或窗口内无事件"}

    key = "calendar:" + ",".join(sorted(types)) + f":{days}"
    return cached(key, _TTL, _compute)
