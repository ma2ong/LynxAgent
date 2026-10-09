"""拉取价外事件数据，供 rule_audit 的事件规则与 ml_lab 用（2026-10-09）。

都来自东财数据中心（datacenter-web，本机可用；push2 才被墙）：
- repurchase.csv  股票回购（akshare stock_repurchase_em，全历史一次给全）
- holder_up.csv / holder_down.csv  股东增持 / 减持（stock_ggcg_em）
- survey.csv  机构调研（RPT_ORG_SURVEY，NUMBERNEW=1 每次调研一行，SUM=参与机构数）。akshare 用的
  RPT_ORG_SURVEYNEW 只保留近一年，这里换表、按季度分段直接拉

输出 experiments/.cache/*.csv，不入库，可随时重拉。约 20 分钟；只重拉某个：python experiments/fetch_events.py survey.csv
"""
from __future__ import annotations

import os
import sys
import time

import pandas as pd
import requests

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def _retry(fn, label):
    for attempt in range(3):
        try:
            return fn()
        except Exception as exc:  # 网络抖动重试，三次都失败就跳过这一段并说出来
            print(label, "retry", attempt, repr(exc)[:120], flush=True)
            time.sleep(3)
    print(label, "放弃", flush=True)
    return None


def _survey_window(lo, hi) -> list[pd.DataFrame]:
    flt = f"(NUMBERNEW=\"1\")(NOTICE_DATE>='{lo:%Y-%m-%d}')(NOTICE_DATE<'{hi:%Y-%m-%d}')"
    frames, page, pages = [], 1, 1
    while page <= pages:
        # 这张表 pageSize 超过 50 一律回「系统繁忙」，不是限流
        params = {"pageSize": "50", "pageNumber": str(page), "reportName": "RPT_ORG_SURVEY",
                  "columns": "SECURITY_CODE,NOTICE_DATE,SUM", "source": "WEB", "client": "WEB", "filter": flt}
        for wait in (0, 2, 5, 15, 30):   # 连续翻页偶尔回「系统繁忙」，退避后重试同一页
            time.sleep(wait)
            j = _retry(lambda: requests.get(URL, params=params, timeout=20).json(), f"survey {lo:%Y-%m-%d} p{page}")
            if (j or {}).get("message") == "ok":
                break
        else:
            print(f"survey {lo:%Y-%m-%d} p{page}/{pages} 失败，本段数据不完整", flush=True)
        res = (j or {}).get("result") or {}
        if not res.get("data"):
            break
        pages = res.get("pages", 1)
        frames.append(pd.DataFrame(res["data"]))
        page += 1
    print(f"survey {lo:%Y-%m-%d}: {sum(len(f) for f in frames)} 条 / {pages} 页", flush=True)
    return frames


def fetch_survey() -> pd.DataFrame:
    from concurrent.futures import ThreadPoolExecutor
    # 按周分段：每段十来页。按季度时翻到第 50 页左右就开始连续失败
    weeks = pd.date_range("2019-09-30", pd.Timestamp.today() + pd.Timedelta(days=7), freq="W-MON")
    with ThreadPoolExecutor(4) as ex:
        parts = ex.map(lambda q: _survey_window(*q), zip(weeks[:-1], weeks[1:]))
        frames = [f for part in parts for f in part]
    return pd.concat(frames, ignore_index=True)


def main() -> None:
    import akshare as ak
    os.makedirs(CACHE, exist_ok=True)
    only = set(sys.argv[1:])
    jobs = {
        "repurchase.csv": lambda: ak.stock_repurchase_em(),
        "holder_up.csv": lambda: ak.stock_ggcg_em(symbol="股东增持"),
        "holder_down.csv": lambda: ak.stock_ggcg_em(symbol="股东减持"),
        "survey.csv": fetch_survey,
    }
    for name, fn in jobs.items():
        if only and name not in only:
            continue
        df = _retry(fn, name)
        if df is None or df.empty:
            continue
        df.to_csv(os.path.join(CACHE, name), index=False, encoding="utf-8")
        print("saved", name, len(df), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
