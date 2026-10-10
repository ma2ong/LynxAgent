"""拉 2017–2026 定期报告（业绩报表），给 event_lab.py 做业绩超预期漂移（PEAD）研究（2026-10-10）。

东财数据中心 RPT_LICO_FN_CPD：每只股票每期一行，NOTICE_DATE 是首次披露日（akshare stock_yjbb_em
给的「最新公告日期」是之后的更正日期，不能当事件日）。EITIME 是披露时刻，收盘后披露的下一交易日才能交易。
净利润是累计值，单季度 = 本期累计 − 上期累计（一季报就是本身）。

输出 experiments/.cache/reports.csv。
"""
from __future__ import annotations

import sys
import time

import pandas as pd
import requests

from fetch_events import CACHE, URL

COLS = ("SECURITY_CODE,REPORTDATE,NOTICE_DATE,EITIME,BASIC_EPS,TOTAL_OPERATE_INCOME,PARENT_NETPROFIT,"
        "YSTZ,SJLTZ,WEIGHTAVG_ROE,XSMLL")


def period(rd: str) -> pd.DataFrame:
    frames, page, pages = [], 1, 1
    while page <= pages:
        params = {"reportName": "RPT_LICO_FN_CPD", "columns": COLS, "pageSize": "500", "pageNumber": str(page),
                  "filter": f"(REPORTDATE='{rd}')", "source": "WEB", "client": "WEB"}
        for wait in (0, 3, 10, 30):
            time.sleep(wait)
            try:
                j = requests.get(URL, params=params, timeout=30).json()
                if j.get("message") == "ok":
                    break
            except Exception as e:  # 网络抖动退避重试
                print(rd, page, repr(e)[:80], flush=True)
        else:
            print(rd, f"p{page} 失败，本期不完整", flush=True)
            page += 1
            continue
        res = j.get("result") or {}
        if not res.get("data"):
            break
        pages = res["pages"]
        frames.append(pd.DataFrame(res["data"]))
        page += 1
    print(rd, sum(len(f) for f in frames), flush=True)
    return pd.concat(frames) if frames else pd.DataFrame()


def main():
    rds = [f"{y}-{md}" for y in range(2017, 2027) for md in ("03-31", "06-30", "09-30", "12-31")]
    rds = [r for r in rds if r <= time.strftime("%Y-%m-%d")]
    out = pd.concat([period(r) for r in rds], ignore_index=True)
    out.to_csv(CACHE + "/reports.csv", index=False, encoding="utf-8")
    print("saved", len(out))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
