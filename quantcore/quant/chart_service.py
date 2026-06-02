"""组装前端 K 线图所需数据：蜡烛 + 均线 + MACD + 命中形态。"""
from __future__ import annotations
from typing import Dict, List, Optional

import pandas as pd

from .data import load_local_kline
from .factors import enrich_indicators
from .integrations import recognize_patterns


def _ma(close: pd.Series, n: int) -> List[Optional[float]]:
    s = close.rolling(n).mean()
    return [None if pd.isna(v) else round(float(v), 3) for v in s]


def _arr(series: pd.Series, ndigits: int = 2) -> List[Optional[float]]:
    return [None if pd.isna(v) else round(float(v), ndigits) for v in series]


def _macd(close: pd.Series):
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = (dif - dea) * 2
    f = lambda s: [round(float(v), 4) for v in s]
    return {"dif": f(dif), "dea": f(dea), "hist": f(hist)}


# 各形态在检测时使用的近端 K 线窗口（与 _detect_pre_lift_patterns 的 tail(N) 对应），
# 据此把形态精确框选到它实际发生的 K 线区域；名称做子串匹配，未知默认 20 根。
_PATTERN_WINDOW = [
    ("均线粘合", 45), ("地量", 22), ("挖坑", 14), ("压力位", 35), ("试盘", 35),
    ("MACD", 32), ("空中加油", 32), ("底背离", 32), ("小步快跑", 9), ("红三兵", 9),
    ("盘口", 18), ("压盘", 18), ("RPS", 60), ("海龟", 22), ("涨停洗盘", 12), ("突破", 22),
]


def _pattern_window(name: str) -> int:
    for kw, n in _PATTERN_WINDOW:
        if kw in (name or ""):
            return n
    return 20


def build_chart_payload(symbol: str, name: str = "", days: int = 250) -> Dict[str, object]:
    df = load_local_kline(symbol, days=days)
    if df is None or df.empty:
        return {"symbol": symbol, "name": name, "dates": [], "candles": [],
                "volumes": [], "ma": {}, "macd": {}, "patterns": []}
    df = df.reset_index(drop=True)
    close = df["close"].astype(float)
    dates = [str(d)[:10] for d in df["date"]]
    candles = [[round(float(o), 3), round(float(c), 3), round(float(l), 3), round(float(h), 3)]
               for o, c, l, h in zip(df["open"], df["close"], df["low"], df["high"])]
    volumes = [round(float(v), 1) for v in df["volume"]]
    ma = {f"ma{n}": _ma(close, n) for n in (5, 10, 20, 30, 60)}
    macd = _macd(close)
    # 副图指标序列（KDJ / ADX / 资金流），失败不影响主图
    kdj: Dict[str, object] = {}
    adx: Dict[str, object] = {}
    moneyflow: Dict[str, object] = {}
    try:
        ind = enrich_indicators(df)
        kdj = {"k": _arr(ind["kdj_k"]), "d": _arr(ind["kdj_d"]), "j": _arr(ind["kdj_j"])}
        adx = {"adx": _arr(ind["adx14"]), "plus_di": _arr(ind["plus_di"]), "minus_di": _arr(ind["minus_di"])}
        moneyflow = {"mfi": _arr(ind["mfi14"]), "cmf": _arr(ind["cmf20"], 4)}
    except Exception:
        pass
    patterns: List[Dict[str, object]] = []
    try:
        rec = recognize_patterns(symbol, df)
        n = len(df)
        for p in rec.patterns:
            if not p.get("active"):
                continue
            window = _pattern_window(str(p.get("name") or ""))
            start = max(0, n - window)
            end = n - 1
            patterns.append({
                **p,
                "start_index": start,
                "end_index": end,
                "start_date": dates[start],
                "end_date": dates[end],
            })
    except Exception:
        patterns = []
    return {"symbol": symbol, "name": name, "dates": dates, "candles": candles,
            "volumes": volumes, "ma": ma, "macd": macd, "patterns": patterns,
            "kdj": kdj, "adx": adx, "moneyflow": moneyflow}
