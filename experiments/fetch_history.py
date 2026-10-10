"""拉 2013–2019 全市场日线（含期间退市股），给 ml_history.py 做模型没见过的老数据回测（2026-10-10）。

本地库从 2020 年开始，2013–2019 这 7 年模型从未见过。新浪日K（akshare stock_zh_a_daily）：
前复权价格 + 真实成交额。新浪查不到的退市股（几乎全部）改用腾讯日K：先试前复权，没有就用不复权
（只差除权缺口），成交额按 成交量(手)×100×不复权收盘 估算。
腾讯 fqkline 连拉几百只就被 WAF 拦（2026-10-10 实测），所以换新浪、慢速、逐只缓存可续传。
股票名单 = 本地库现存全部 + 交易所退市列表里 2013 年后退市的。查不到的退市股会说出来。

输出 experiments/.cache/kline_2013_2019.parquet（逐只缓存在 .cache/hist_parts/）。约 1 小时。
"""
from __future__ import annotations

import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "experiments" / ".cache"
PARTS = CACHE / "hist_parts"
START, END = "20130101", "20191231"


def fetch(code: str) -> bool:
    """拉一只写到 hist_parts；返回是否有数据。已缓存的直接跳过。"""
    import akshare as ak
    out, empty = PARTS / f"{code}.parquet", PARTS / f"{code}.empty"
    if out.exists() or empty.exists():
        return out.exists()
    mk = ("sh" if code.startswith("6") else "sz") + code
    df = None
    for adjust in ("qfq", ""):
        for wait in (0, 3):   # 新浪对个别股票稳定报错（成交额接口空），不是限流，不必久等
            time.sleep(wait + 0.4)
            try:
                df = ak.stock_zh_a_daily(symbol=mk, start_date=START, end_date=END, adjust=adjust)
                break
            except Exception as e:  # 无复权因子 / 无数据 / 网络：先重试，再退回不复权
                err = e
        if df is not None:
            break
    if df is None or df.empty:
        print(f"{code} 无数据：{err!r}"[:160] if df is None else f"{code} 无数据", flush=True)
        empty.touch()
        return False
    df = df.assign(symbol=code, date=pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d"))
    df[["symbol", "date", "open", "high", "low", "close", "amount"]].to_parquet(out, index=False)
    return True


def fetch_tencent(code: str) -> bool:
    """新浪没有的退市股走腾讯；成功就删掉 .empty 标记。"""
    import requests
    mk = ("sh" if code.startswith("6") else "sz") + code
    got = {}
    for fq in ("qfq", ""):
        time.sleep(0.3)
        try:
            j = requests.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                             params={"param": f"{mk},day,2013-01-01,2019-12-31,2000,{fq}"}, timeout=20).json()
            d = (j.get("data") or {}).get(mk) or {}
            got[fq] = d.get("qfqday") or d.get("day") or []
        except Exception as e:  # 腾讯会间歇性 WAF 拦截，失败就说出来，不影响其余
            print(f"{code} 腾讯 {fq or 'raw'} 失败：{e!r}"[:120], flush=True)
            got[fq] = []
    bars = got["qfq"] or got[""]
    if not bars:
        return False
    raw = {b[0]: float(b[2]) for b in got[""]}
    df = pd.DataFrame([(code, b[0], *map(float, b[1:6])) for b in bars],
                      columns=["symbol", "date", "open", "close", "high", "low", "vol"])
    df["amount"] = df["vol"] * 100 * df["date"].map(raw).fillna(df["close"])
    df[["symbol", "date", "open", "high", "low", "close", "amount"]].to_parquet(PARTS / f"{code}.parquet", index=False)
    (PARTS / f"{code}.empty").unlink(missing_ok=True)
    return True


def fetch_sina_raw(code: str) -> bool:
    """新浪原始日K（akshare 同一解码），跳过 akshare 里对退市股必挂的流通股本接口；
    前复权 = 不复权价 ÷ 新浪 qfq 因子（与 akshare 的算法相同），拿不到因子就用不复权。"""
    import requests
    import py_mini_racer
    from akshare.stock.cons import hk_js_decode, zh_sina_a_stock_hist_url, zh_sina_a_stock_qfq_url
    mk = ("sh" if code.startswith("6") else "sz") + code
    try:
        r = requests.get(zh_sina_a_stock_hist_url.format(mk), timeout=20)
        js = py_mini_racer.MiniRacer()
        js.eval(hk_js_decode)
        d = pd.DataFrame(js.call("d", r.text.split("=")[1].split(";")[0].replace('"', "")))
    except Exception as e:  # 无数据 / 被拦：说出来，按缺失处理
        print(f"{code} 新浪原始K 失败：{e!r}"[:120], flush=True)
        return False
    if "date" not in d:
        return False
    d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    d = d[(d["date"] >= "2013-01-01") & (d["date"] <= "2019-12-31")].copy()
    if d.empty:
        return False
    for c in ("open", "high", "low", "close", "amount"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    try:
        t = requests.get(zh_sina_a_stock_qfq_url.format(mk), timeout=20).text
        f = pd.DataFrame(eval(t.split("=")[1].split("\n")[0])["data"]).rename(columns={"d": "date", "f": "f"})
        f["f"] = f["f"].astype(float)
        d["dt"], f["dt"] = pd.to_datetime(d["date"]), pd.to_datetime(f["date"])
        d = pd.merge_asof(d.sort_values("dt"), f.drop(columns="date").sort_values("dt"), on="dt")
        d["f"] = d["f"].fillna(f.sort_values("date")["f"].iloc[0])
        for c in ("open", "high", "low", "close"):
            d[c] = d[c] / d["f"]
    except Exception as e:  # 没有复权因子的退市股退回不复权
        print(f"{code} 无复权因子，用不复权：{e!r}"[:100], flush=True)
    d = d.assign(symbol=code)
    d[["symbol", "date", "open", "high", "low", "close", "amount"]].to_parquet(PARTS / f"{code}.parquet", index=False)
    (PARTS / f"{code}.empty").unlink(missing_ok=True)
    return True


def main():
    conn = sqlite3.connect(f"file:{ROOT / 'runtime' / 'quant_data.sqlite'}?mode=ro", uri=True)
    alive = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM stock_meta")]
    conn.close()
    dl = pd.concat([pd.read_csv(CACHE / f, dtype=str).iloc[:, :4].set_axis(["code", "name", "list", "out"], axis=1)
                    for f in ("delist_sh.csv", "delist_sz.csv")])
    dead = dl[dl["out"].astype(str) >= START]["code"].str.zfill(6).tolist()
    codes = sorted({c for c in alive + dead if not c.startswith(("8", "4", "92"))})
    print(f"现存 {len(alive)} 只 + 2013 后退市 {len(dead)} 只 → 去掉北交所共 {len(codes)} 只", flush=True)
    PARTS.mkdir(exist_ok=True)
    missing_dead = []
    with ThreadPoolExecutor(2) as ex:
        for i, (code, ok) in enumerate(zip(codes, ex.map(fetch, codes))):
            if not ok and code in dead:
                missing_dead.append(code)
            if i % 250 == 0:
                print(f"{i}/{len(codes)}", flush=True)
    for code in list(missing_dead):
        if fetch_sina_raw(code) or fetch_tencent(code):
            missing_dead.remove(code)
    out = pd.concat([pd.read_parquet(f) for f in PARTS.glob("*.parquet")], ignore_index=True)
    out = out.dropna(subset=["open", "close"])
    out.to_parquet(CACHE / "kline_2013_2019.parquet", index=False)
    print(f"{out['symbol'].nunique()} 只有日线，共 {len(out)} 行；退市股查不到 {len(missing_dead)}/{len(dead)} 只")

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
