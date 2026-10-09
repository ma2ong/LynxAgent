"""把 2020-09 以来退市股票的日线拉下来，给 portfolio_lab.py 修存活偏差用（2026-10-09）。

名单：akshare 交易所退市列表（stock_info_sh_delist / stock_info_sz_delist），落在
experiments/.cache/delisted_list.csv。日线：腾讯不复权日K（退市股仍可查）。
腾讯不给成交额，按 成交量(手)×100×收盘价 估算 —— 只用于 3000 万流动性门槛，够用。

用法：python experiments/fetch_delisted.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "experiments" / ".cache"


def main():
    lst = CACHE / "delisted_list.csv"
    if not lst.exists():
        import akshare as ak
        sh = ak.stock_info_sh_delist(symbol="全部").iloc[:, :4]
        sz = ak.stock_info_sz_delist(symbol="终止上市公司").iloc[:, :4]
        sh.columns = sz.columns = ["code", "name", "list", "out"]
        d = pd.concat([sh, sz])
        d[d["out"].astype(str) >= "2020-09-01"].to_csv(lst, index=False)
    codes = pd.read_csv(lst, dtype=str)["code"].str.zfill(6)
    rows = []
    for i, code in enumerate(codes):
        mk = ("sh" if code.startswith("6") else "sz") + code
        try:
            j = requests.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                             params={"param": f"{mk},day,2020-01-01,2026-12-31,2000,"}, timeout=15).json()
            data = (j.get("data") or {}).get(mk) or {}
            bars = data.get("day") or data.get("qfqday") or []
        except Exception as e:  # 单只失败记一笔，不影响其余
            print(f"{code} 失败：{e!r}")
            bars = []
        for b in bars:
            o, c, h, l, v = map(float, b[1:6])
            rows.append((code, b[0], o, h, l, c, v * 100 * c))
        if i % 25 == 0:
            print(f"{i}/{len(codes)}")
        time.sleep(0.2)
    out = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "amount"])
    out.to_csv(CACHE / "delisted_kline.csv", index=False)
    print(f"{out['symbol'].nunique()}/{len(codes)} 只有日线，共 {len(out)} 行")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
