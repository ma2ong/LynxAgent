"""顶部宏观指标条：三大指数实时快照（腾讯 s_ 简版行情）。

字段序（~ 分隔）：0 市场 1 名称 2 代码 3 现价 4 涨跌 5 涨跌幅% 6 成交量(手) 7 成交额(万)。
解析与网络分离：parse_index_payload 纯函数可测，fetch_index_quotes 负责请求。
"""
from __future__ import annotations

import re
from typing import Dict, List

import requests

INDEX_CODES = [("sh000001", "上证指数"), ("sz399001", "深证成指"), ("sz399006", "创业板指")]


def parse_index_payload(text: str) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    for m in re.finditer(r'v_s_(sh|sz)(\d{6})="([^"]*)"', text):
        fields = m.group(3).split("~")
        if len(fields) < 6:
            continue

        def _f(idx: int):
            try:
                return float(fields[idx])
            except (ValueError, IndexError):
                return None

        price = _f(3)
        if price is None:
            continue
        out.append({
            "code": m.group(1) + m.group(2),
            "name": fields[1],
            "price": price,
            "change": _f(4),
            "change_percent": _f(5),
            "amount_wan": _f(7),
        })
    return out


def fetch_index_history(as_of: str = "", window: int = 5) -> List[Dict[str, object]]:
    """三大指数「截至 as_of」的最新一日涨跌% 与近 window 日累计涨跌%。

    大盘环境的指数口径：用户口中的「大盘」就是这三个指数，而全市场个股中位数回答的是
    另一个问题（我的票好不好做）。两个口径都报，背离才看得见。
    as_of 用于对齐个股口径的截止日——否则盘中会拿到今天的未完成 bar，横幅自相矛盾。
    失败抛异常由调用方兜底。
    """
    session = requests.Session()
    session.trust_env = False
    out: List[Dict[str, object]] = []
    for code, name in INDEX_CODES:
        resp = session.get(
            f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{window + 4},",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8,
        )
        resp.raise_for_status()
        data = (resp.json().get("data") or {}).get(code) or {}
        rows = data.get("day") or data.get("qfqday") or []
        closes = []
        for row in rows:
            if len(row) < 3:
                continue
            day = str(row[0])
            if as_of and day > as_of:
                continue
            try:
                closes.append((day, float(row[2])))
            except (TypeError, ValueError):
                continue
        if len(closes) < 2 or closes[-1][1] <= 0:
            continue
        last = closes[-1][1]
        prev = closes[-2][1]
        base = closes[-(window + 1)][1] if len(closes) > window else closes[0][1]
        out.append({
            "code": code,
            "name": name,
            "date": closes[-1][0],
            "last_pct": round((last / prev - 1) * 100, 2) if prev > 0 else 0.0,
            "window_pct": round((last / base - 1) * 100, 2) if base > 0 else 0.0,
        })
    return out


def fetch_index_quotes() -> List[Dict[str, object]]:
    """拉取三大指数简版行情；失败抛异常由调用方兜底。"""
    query = ",".join(f"s_{code}" for code, _ in INDEX_CODES)
    session = requests.Session()
    session.trust_env = False
    resp = session.get(
        f"https://qt.gtimg.cn/q={query}",
        headers={"User-Agent": "Mozilla/5.0"}, timeout=8,
    )
    resp.encoding = "gbk"
    resp.raise_for_status()
    return parse_index_payload(resp.text)
