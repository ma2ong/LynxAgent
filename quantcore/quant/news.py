"""A 股新闻抓取（akshare）。两类消费方：

- 个股近 7 天新闻 -> stock_news()  (研报 A 的"相关新闻"区块)
- 全市场热点资讯流 -> market_news_flow()  (流水线 B 的题材 Agent 输入)

所有函数失败时返回空列表，不抛栈（调用方据此降级）。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List


def _to_dt(value) -> datetime | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[: len(fmt) + 2].strip(), fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _stock_news_direct(symbol: str, limit: int = 10) -> List[Dict[str, str]]:
    """直接调 Eastmoney search API，绕过 akshare stock_news_em 内部的 pyarrow 正则 bug。"""
    import json
    import requests

    url = "https://search-api-web.eastmoney.com/search/jsonp"
    inner = {
        "uid": "", "keyword": symbol, "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {
            "searchScope": "default", "sort": "default",
            "pageIndex": 1, "pageSize": min(limit, 20),
            "preTag": "<em>", "postTag": "</em>",
        }},
    }
    cb = "jQuery112404979052495353642_1"
    params = {"cb": cb, "param": json.dumps(inner, ensure_ascii=False), "_": "1"}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"https://so.eastmoney.com/news/s?keyword={symbol}",
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        text = r.text.strip()
        # JSONP 解包：去掉任意 callback(...)  包装
        start = text.find("(")
        end = text.rfind(")")
        if start != -1 and end != -1:
            text = text[start + 1:end]
        data = json.loads(text)
        items = data.get("result", {}).get("cmsArticleWebOld", []) or []
    except Exception:
        return []

    results = []
    for item in items:
        code = str(item.get("code") or "")
        results.append({
            "time": str(item.get("date") or "").strip().replace("　", ""),
            "title": str(item.get("title") or "").replace("<em>", "").replace("</em>", "").replace("　", "").strip(),
            "source": str(item.get("mediaName") or "").strip(),
            "url": f"http://finance.eastmoney.com/a/{code}.html" if code else "",
        })
    return results


def stock_news(symbol: str, days: int = 7, limit: int = 10) -> List[Dict[str, str]]:
    """个股近 N 天新闻：[{time, title, source, url}]，按时间倒序。"""
    symbol = str(symbol).zfill(6)
    cutoff = datetime.now() - timedelta(days=days)

    # akshare stock_news_em 在 pyarrow 字符串后端下会 \u 正则报错；关闭后端即可
    try:
        import pandas as pd
        import akshare as ak
        pd.options.future.infer_string = False
        df = ak.stock_news_em(symbol=symbol)
    except Exception:
        # 再降级：直接调 Eastmoney API
        raw = _stock_news_direct(symbol, limit=limit)
        items_fb: List[Dict[str, str]] = []
        for item in raw:
            dt = _to_dt(item.get("time"))
            if dt and dt < cutoff:
                continue
            items_fb.append(item)
        return items_fb[:limit]
    if df is None or df.empty:
        return []

    cols = {c: c for c in df.columns}
    title_col = next((c for c in ("新闻标题", "标题") if c in cols), None)
    time_col = next((c for c in ("发布时间", "时间") if c in cols), None)
    src_col = next((c for c in ("文章来源", "来源") if c in cols), None)
    url_col = next((c for c in ("新闻链接", "链接") if c in cols), None)
    if not title_col:
        return []

    items = []
    for _, row in df.iterrows():
        dt = _to_dt(row.get(time_col)) if time_col else None
        if dt and dt < cutoff:
            continue
        items.append(
            {
                "time": str(row.get(time_col, "")).strip() if time_col else "",
                "title": str(row.get(title_col, "")).strip(),
                "source": str(row.get(src_col, "")).strip() if src_col else "",
                "url": str(row.get(url_col, "")).strip() if url_col else "",
            }
        )
    items.sort(key=lambda x: x["time"], reverse=True)
    return items[:limit]


def market_news_flow(limit: int = 500) -> List[Dict[str, str]]:
    """全市场财经快讯流（财联社电报为主，失败回退东财全球财经）：[{time, title, content}]。

    供题材 Agent 做分词/热点板块打分。返回最多 limit 条，按时间倒序。
    """
    rows = _cls_telegraph(limit) or _em_global(limit)
    return rows[:limit]


def _cls_telegraph(limit: int) -> List[Dict[str, str]]:
    try:
        import akshare as ak

        df = ak.stock_info_global_cls(symbol="全部")
    except Exception:
        return []
    if df is None or df.empty:
        return []
    title_col = next((c for c in ("标题", "title") if c in df.columns), None)
    content_col = next((c for c in ("内容", "content") if c in df.columns), None)
    date_col = next((c for c in ("发布日期", "日期") if c in df.columns), None)
    time_col = next((c for c in ("发布时间", "时间") if c in df.columns), None)
    out: List[Dict[str, str]] = []
    for _, row in df.head(limit).iterrows():
        ts = " ".join(s for s in (str(row.get(date_col, "")).strip() if date_col else "",
                                  str(row.get(time_col, "")).strip() if time_col else "") if s)
        out.append(
            {
                "time": ts,
                "title": str(row.get(title_col, "")).strip() if title_col else "",
                "content": str(row.get(content_col, "")).strip() if content_col else "",
            }
        )
    return out


def _em_global(limit: int) -> List[Dict[str, str]]:
    try:
        import akshare as ak

        df = ak.stock_info_global_em()
    except Exception:
        return []
    if df is None or df.empty:
        return []
    title_col = next((c for c in ("标题", "title") if c in df.columns), None)
    content_col = next((c for c in ("摘要", "内容") if c in df.columns), None)
    time_col = next((c for c in ("发布时间", "时间") if c in df.columns), None)
    out: List[Dict[str, str]] = []
    for _, row in df.head(limit).iterrows():
        out.append(
            {
                "time": str(row.get(time_col, "")).strip() if time_col else "",
                "title": str(row.get(title_col, "")).strip() if title_col else "",
                "content": str(row.get(content_col, "")).strip() if content_col else "",
            }
        )
    return out
