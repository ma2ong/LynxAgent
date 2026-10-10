"""抛开智选现有 8 因子：十条新思路规则，每天只挑 2 只（2026-10-10）。

Allen 要求跳出固定思维、只回测不改代码。每条规则直接从全市场原始价量里每天挑 2 只，
不经过智选的打分。规则在看结果前写定（RULES），两个年代分开检验：
  老年代 2015-07 ~ 2019-12（fetch_history.py，含退市股）
  新年代 2021-01 ~ 2026-09（本地库 + 2020 年后退市股）
口径：信号日收盘后出，次日开盘买、第 6 个交易日开盘卖（持 5 天）；开盘即涨停买不到的剔除；
对同日可投资池（非北交所、满 250 根、20 日均额 ≥3000 万）等权比；为避免持有期重叠，每 5 个交易日取一天。
判定：两个年代都「平均超额 > 0、t ≥ 2、胜率 > 50%、多数年份为正」才算。十条一起看，单条 t≈2 要打折（多重检验）。
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


def _pick(x: pd.DataFrame, mask, key: str, asc: bool) -> pd.DataFrame:
    x = x[mask(x)] if mask is not None else x
    return x.nsmallest(2, key) if asc else x.nlargest(2, key)


# 名称 → (过滤条件, 排序键, 升序?)
RULES = {
    "趋势中超跌（年线上、5日跌最多）": (lambda x: x.dma250 > 0, "ret5", True),
    "放量不涨（吸筹）": (lambda x: x.dr.abs() < 0.01, "amt_r1", False),
    "强收盘温和放量（涨2-6%收最高）": (lambda x: x.dr.between(0.02, 0.06) & (x.clv > 0.8) & x.amt_r5.between(1.2, 2.5), "ind_ret20", False),
    "低波稳健上行（60/250日线上波动最小）": (lambda x: (x.dma60 > 0) & (x.dma250 > 0), "vol20", True),
    "最强行业里的回调": (lambda x: (x.ind_rank <= 5) & (x.ret5 < 0), "ret5", True),
    "大跌后企稳（20日内有-7%、今日收红、贴近低点）": (lambda x: (x.min20 <= -0.07) & (x.dr > 0), "dlo20", True),
    "长强短弱（半年强、5日弱）": (None, "lt_st", False),
    "底部放量（距年高-40%以下、量放1.5倍）": (lambda x: (x.dhi250 <= -0.4) & (x.amt_r20 >= 1.5), "amt_r20", False),
    "冷门低换手（20日线上、成交额最小）": (lambda x: x.dma20 > 0, "lamt20", True),
    "年内新高且没涨停过（波动最小）": (lambda x: (x.dhi250 >= -0.001) & (x.lu20 == 0), "vol20", True),
}


def evaluate(k: pd.DataFrame, start: str, end: str, label: str) -> list[dict]:
    k, _ = build_features(k)
    u = k[(k.bars >= MIN_BARS) & (k.amt20 >= MIN_AMT) & (k.date >= start) & (k.date <= end)
          & k.fwd.notna() & ~k.next_limit_up].copy()
    del k
    u["ind_rank"] = u.groupby("date")["ind_ret20"].rank(method="dense", ascending=False)
    u["lt_st"] = u.groupby("date")["ret120"].rank(pct=True) - u.groupby("date")["ret5"].rank(pct=True)
    days = sorted(u.date.unique())[::5]
    u = u[u.date.isin(days)]
    rows = []
    print(f"\n== {label}：{days[0]} ~ {days[-1]}，{len(days)} 个信号日（每 5 天一个，不重叠） ==", flush=True)
    for name, (mask, key, asc) in RULES.items():
        per, hits = [], []
        for d, x in u.groupby("date"):
            base = x.fwd.mean()
            sel = _pick(x, mask, key, asc)
            if len(sel):
                per.append((d, sel.fwd.mean() - base))
                hits += list(sel.fwd > base)
        t = pd.DataFrame(per, columns=["d", "ex"])
        ex = t.ex
        tt = ex.mean() / ex.std() * np.sqrt(len(ex)) if len(ex) > 2 else np.nan
        yr = t.groupby(t.d.str[:4]).ex.mean() * 100
        rows.append({"年代": label, "规则": name, "超额pp": ex.mean() * 100, "t": tt, "胜率": np.mean(hits),
                     "天数": len(ex), "正年份": f"{(yr > 0).sum()}/{len(yr)}"})
        print(f"{name:28s} 每5天超额 {ex.mean()*100:+.2f}pp t={tt:+.2f} 单票胜率 {np.mean(hits):.0%} "
              f"({len(ex)}天) 逐年 " + " ".join(f"{y}:{v:+.1f}" for y, v in yr.items()), flush=True)
    return rows


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    rows = []
    old = pd.read_parquet(CACHE / "kline_2013_2019.parquet")
    old = old[(old.open > 0) & (old.close > 0) & (old.amount > 0)].drop_duplicates(["symbol", "date"])
    old = old.astype({c: "float32" for c in ("open", "high", "low", "close")} | {"amount": "float64"})
    rows += evaluate(old.sort_values(["symbol", "date"]).reset_index(drop=True), "2015-07-01", "2019-12-31", "老年代")
    del old
    dl = CACHE / "delisted_kline.csv"
    new = load_kline(ROOT / "runtime" / "quant_data.sqlite",
                     pd.read_csv(dl, dtype={"symbol": str}) if dl.exists() else None, since="2020-01-01")
    rows += evaluate(new, "2021-01-01", "2026-09-30", "新年代")
    r = pd.DataFrame(rows)
    r.to_csv(ROOT / "experiments" / "results" / "rules_top2.csv", index=False, encoding="utf-8-sig")
    ok = r.groupby("规则").apply(lambda x: ((x["超额pp"] > 0) & (x["t"] >= 2) & (x["胜率"] > 0.5)).all(), include_groups=False)
    print("\n两个年代都过关的规则：", [n for n, v in ok.items() if v] or "无")


if __name__ == "__main__":
    main()
