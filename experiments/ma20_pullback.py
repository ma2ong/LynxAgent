"""强势股回踩 20 日均线：碰线后有没有一波反弹（2026-10-10，Allen 提）。

说法：前期热门 / 趋势好的股，阶段性回调到 20 日线，碰到后会有一波不错的反弹或反转。
定义（看结果前写定）—— 在 T 日收盘时判断：
  趋势好    20 日线向上（MA20 高于 5 天前）且收盘在 60 日线上
  前期强    60 日涨幅 ≥30%（变体「热门」：近 20 日有过涨停）
  回踩到线  近 10 日里收盘曾高出 20 日线 8% 以上（是从上面跌回来的），今天最低价碰到 20 日线（≤ MA20×1.01）
  守住      收盘 ≥ MA20×0.99（对照组：收盘跌破 20 日线 1% 以上）
变体：收阳确认（当天收盘 > 开盘）、缩量回踩（近 5 日成交额 < 20 日均额的 80%）
口径：T+1 开盘买，T+1+H 开盘卖（H = 5 / 10 / 20），对同日可投资池均值算超额；开盘即涨停买不到的剔除；
t 按信号日聚类。两个年代（2015-07~2019-12 含退市股 / 2021-01~2026-09 含退市股）分开报。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from quantcore.quant.ml_factors import MIN_AMT, MIN_BARS, build_features, load_kline  # noqa: E402

CACHE = ROOT / "experiments" / ".cache"
H = (5, 10, 20)


def prepare(k: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    k, _ = build_features(k)
    g = k.groupby("symbol", sort=False)
    k["ma20"] = k["close"] / (1 + k["dma20"])
    k["ma20_up"] = k["ma20"] > g["ma20"].shift(5)
    k["above_peak10"] = (k["close"] / k["ma20"]).groupby(k["symbol"], sort=False).transform(
        lambda s: s.shift(1).rolling(10, min_periods=5).max())
    k["nxt_open"] = g["open"].shift(-1)
    for h in H:
        k[f"r{h}"] = (g["open"].shift(-(1 + h)) / k["nxt_open"] - 1).clip(-0.6, 1.5)
    u = k[(k.bars >= MIN_BARS) & (k.amt20 >= MIN_AMT) & (k.date >= start) & (k.date <= end)
          & ~k.next_limit_up & k.nxt_open.notna()].copy()
    for h in H:
        u[f"ex{h}"] = u[f"r{h}"] - u.groupby("date")[f"r{h}"].transform("mean")
    return u


def rules(u: pd.DataFrame) -> dict[str, pd.Series]:
    trend = u.ma20_up & (u.dma60 > 0)
    strong = u.ret60 >= 0.30
    hot = u.lu20 >= 1
    came_down = u.above_peak10 >= 1.08
    touch = u.low <= u.ma20 * 1.01
    hold = u.close >= u.ma20 * 0.99
    broke = u.close < u.ma20 * 0.99
    base = trend & strong & came_down & touch
    return {
        "强势股回踩20日线·守住": base & hold,
        "  + 收阳确认": base & hold & (u.close > u.open),
        "  + 缩量回踩": base & hold & (u.amt_r5 < 0.8),
        "热门股（近20日涨停过）回踩·守住": trend & hot & came_down & touch & hold,
        "对照：强势股回踩但收盘跌破20日线": base & broke,
        "对照：强势股（不管回踩）": trend & strong,
    }


def report(u: pd.DataFrame, label: str) -> None:
    print(f"\n== {label}：{u.date.min()} ~ {u.date.max()} ==", flush=True)
    for name, mask in rules(u).items():
        x = u[mask]
        line = f"{name:30s} 信号 {len(x):6d} 个"
        for h in H:
            per_day = x.groupby("date")[f"ex{h}"].mean().dropna()
            t = per_day.mean() / per_day.std() * np.sqrt(len(per_day)) if len(per_day) > 2 else np.nan
            line += f" | T+{h}: {x[f'ex{h}'].mean()*100:+.2f}pp 胜率{(x[f'ex{h}'] > 0).mean():.0%} t={t:+.1f}"
        yr = x.groupby(x.date.str[:4])["ex10"].mean() * 100
        print(line + "\n" + " " * 32 + "T+10 逐年: " + " ".join(f"{y}:{v:+.1f}" for y, v in yr.items()), flush=True)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    old = pd.read_parquet(CACHE / "kline_2013_2019.parquet")
    old = old[(old.open > 0) & (old.close > 0) & (old.amount > 0)].drop_duplicates(["symbol", "date"])
    old = old.astype({c: "float32" for c in ("open", "high", "low", "close")} | {"amount": "float64"})
    report(prepare(old.sort_values(["symbol", "date"]).reset_index(drop=True), "2015-07-01", "2019-12-31"), "老年代")
    del old
    dl = CACHE / "delisted_kline.csv"
    new = load_kline(ROOT / "runtime" / "quant_data.sqlite",
                     pd.read_csv(dl, dtype={"symbol": str}) if dl.exists() else None, since="2020-01-01")
    report(prepare(new, "2021-01-01", "2026-09-30"), "新年代")


if __name__ == "__main__":
    main()
