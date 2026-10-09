"""多因子 LightGBM 模型的特征与参数（2026-10-09）。

研究在 experiments/ml_lab.py（样本外 2022-07~2026-09：低换手前 50 年化约 +17%、5 年全正，t≈1.3，
不够显著）；线上只用于 ml_shadow 的影子留痕，不进任何给用户看的名单。两边共用这一份，
保证影子记录的就是回测里那个模型。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

INDUSTRY_MAP = Path(__file__).resolve().parents[2] / "runtime" / "industry_map.json"
MIN_AMT = 3e7          # 可投资池：20 日日均成交额 ≥3000 万
MIN_BARS = 250         # 可投资池：上市满 250 根日线
PARAMS = dict(objective="regression", learning_rate=0.05, num_leaves=63, min_data_in_leaf=2000,
              feature_fraction=0.8, bagging_fraction=0.7, bagging_freq=1, lambda_l2=10.0,
              verbose=-1, num_threads=6, seed=7)
ROUNDS = 300


def load_kline(db: str | Path, extra: pd.DataFrame | None = None, since: str | None = None) -> pd.DataFrame:
    """读日线（剔除北交所与空 bar）；extra 是额外并进来的行（研究里用来补退市股）。"""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    sql = "SELECT symbol, date, open, high, low, close, amount FROM daily_kline"
    k = pd.read_sql_query(sql + (" WHERE date >= ?" if since else ""), conn, params=(since,) if since else None)
    conn.close()
    if extra is not None:
        extra = extra[~extra["symbol"].isin(k["symbol"].unique())].drop_duplicates(["symbol", "date"])
        k = pd.concat([k, extra[k.columns]], ignore_index=True)
    k = k[~k["symbol"].str.startswith(("8", "4", "92")) & (k["open"] > 0) & (k["close"] > 0) & (k["amount"] > 0)]
    k = k.astype({c: "float32" for c in ("open", "high", "low", "close")} | {"amount": "float64"})
    return k.sort_values(["symbol", "date"]).reset_index(drop=True)


def rank_universe(k: pd.DataFrame, names: list[str], since: str) -> pd.DataFrame:
    """取可投资池并把特征、标签转成当日横截面分位。"""
    u = k[(k["bars"] >= MIN_BARS) & (k["amt20"] >= MIN_AMT) & (k["date"] >= since)].copy()
    for n in names:
        u[n] = u.groupby("date")[n].rank(pct=True).astype("float32")
    u["y"] = u.groupby("date")["fwd"].rank(pct=True).astype("float32")
    return u


def _roll(g, col, n, fn):
    return g[col].transform(lambda s: getattr(s.rolling(n, min_periods=max(2, n * 3 // 4)), fn)())


def build_features(k: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """逐行特征（只用当日及以前）、可投资池字段与标签 fwd（T+1 开盘买、T+6 开盘卖）。"""
    g = k.groupby("symbol", sort=False)
    c = g["close"]
    k["prev_close"] = c.shift(1)
    k["dr"] = (k["close"] / k["prev_close"] - 1).astype("float32")
    g = k.groupby("symbol", sort=False)
    f = {}
    for n in (1, 3, 5, 10, 20, 60, 120, 250):
        f[f"ret{n}"] = k["close"] / c.shift(n) - 1
    for n in (5, 10, 20, 60, 250):
        f[f"dma{n}"] = k["close"] / _roll(g, "close", n, "mean") - 1
    for n in (5, 20, 60):
        f[f"vol{n}"] = _roll(g, "dr", n, "std")
    f["max20"] = _roll(g, "dr", 20, "max")
    f["min20"] = _roll(g, "dr", 20, "min")
    f["skew20"] = _roll(g, "dr", 20, "skew")
    for n in (20, 60, 250):
        f[f"dhi{n}"] = k["close"] / _roll(g, "high", n, "max") - 1
        f[f"dlo{n}"] = k["close"] / _roll(g, "low", n, "min") - 1
    amt5, amt20, amt60 = (_roll(g, "amount", n, "mean") for n in (5, 20, 60))
    f["lamt20"] = np.log(amt20)
    f["amt_r1"] = k["amount"] / amt20
    f["amt_r5"] = amt5 / amt20
    f["amt_r20"] = amt20 / amt60
    f["amt_cv20"] = _roll(g, "amount", 20, "std") / amt20
    f["illiq20"] = (k["dr"].abs() / k["amount"] * 1e8).groupby(k["symbol"], sort=False).transform(
        lambda s: s.rolling(20, min_periods=15).mean())
    span = (k["high"] - k["low"]).replace(0, np.nan)
    f["clv"] = (k["close"] - k["low"]) / span
    f["upper_sh"] = (k["high"] - np.maximum(k["open"], k["close"])) / k["prev_close"]
    f["intraday"] = k["close"] / k["open"] - 1
    f["gap"] = k["open"] / k["prev_close"] - 1
    lim = np.where(k["symbol"].str.startswith(("300", "301", "688", "689")), 0.195, 0.095)
    f["lu20"] = (k["dr"] >= lim).astype("float32").groupby(k["symbol"], sort=False).transform(
        lambda s: s.rolling(20, min_periods=1).sum())
    f["lprice"] = np.log(k["close"])
    feat = pd.DataFrame(f).astype("float32")
    k = pd.concat([k, feat], axis=1)
    names = list(feat.columns)

    # 行业：个股相对行业、行业自身动量（唯一过闸的价量规则就是 20 日板块动量）
    ind = json.load(open(INDUSTRY_MAP, encoding="utf-8"))
    k["industry"] = k["symbol"].map(ind).fillna("未知")
    for col in ("ret5", "ret20", "ret60"):
        m = k.groupby(["date", "industry"])[col].transform("mean")
        k[f"ind_{col}"] = m.astype("float32")
        k[f"rel_{col}"] = (k[col] - m).astype("float32")
        names += [f"ind_{col}", f"rel_{col}"]

    # 可投资池与标签
    k["bars"] = k.groupby("symbol", sort=False).cumcount()
    nxt_open = k.groupby("symbol", sort=False)["open"].shift(-1)
    exit_open = k.groupby("symbol", sort=False)["open"].shift(-6)
    # 持有期内退市的：按最后一个收盘价卖（否则这些票的标签是空的，又回到只看幸存者）
    last_date = k.groupby("symbol", sort=False)["date"].transform("last")
    last_close = k.groupby("symbol", sort=False)["close"].transform("last")
    delisted = (last_date < k["date"].max()) & exit_open.isna() & nxt_open.notna()
    exit_open = exit_open.where(~delisted, last_close)
    k["fwd"] = (exit_open / nxt_open - 1).clip(-0.6, 1.5)
    k["next_limit_up"] = (nxt_open / k["close"] - 1) >= (lim - 0.002)
    k["amt20"] = amt20
    return k, names
