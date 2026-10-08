"""拉取历年业绩预告（东财数据中心 stock_yjyg_em），供 forecast_drift 事件研究用。

输出 experiments/.cache/forecasts.csv（不入库，可随时重拉）。每期一次请求，约 30 期。
"""
import os
import sys
import time

import pandas as pd

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "forecasts.csv")


def main() -> None:
    import akshare as ak

    periods = [f"{y}{md}" for y in range(2019, 2027) for md in ("0331", "0630", "0930", "1231")]
    periods = [p for p in periods if p <= time.strftime("%Y%m%d")]
    frames = []
    for p in periods:
        for attempt in range(3):
            try:
                df = ak.stock_yjyg_em(date=p)
                break
            except Exception as exc:
                print(p, "retry", attempt, str(exc)[:80], flush=True)
                time.sleep(3)
        else:
            continue
        if df is None or df.empty:
            print(p, "empty", flush=True)
            continue
        df["period"] = p
        frames.append(df)
        print(p, len(df), flush=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    pd.concat(frames, ignore_index=True).to_csv(OUT, index=False, encoding="utf-8")
    print("saved", OUT)


if __name__ == "__main__":
    sys.exit(main())
