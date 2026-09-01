"""市场宽度：有多少只票在涨，而不是指数涨了多少。

为什么单独一页
--------------
系统里本来就在算宽度——regime 模块拿它合成大盘温度、风险预警拿它算破位广度——但对外
只吐一个「偏暖/中性/偏冷」的标签。标签能回答「今天冷不冷」，回答不了「凭什么这么说」，
也看不出「已经冷了几天、在变好还是变坏」。名单变短、加分收紧这些事都挂在这个判断上，
它必须是可核对的，不能是个黑盒。

指标口径
--------
上涨家数 / 上涨占比   最直接的一层：赚钱效应。
站上 20 / 60 日线占比 中期结构。个股口径，与指数无关——指数被权重股主导，
                      「指数横盘但七成票跌破 20 日线」正是它看得见而指数看不见的情形。
20 日新高 / 新低家数   极值扩散。见顶时新高家数往往先于指数收缩。
涨跌停家数            情绪温度，A 股特有的一层。

全部按**个股等权**统计，与 rule_audit 的超额基准、regime 的温度口径同源。
不用指数：选股系统买的是个股，用指数当尺子会把「大票涨小票跌」的日子读成普涨。
"""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

# 涨跌停判定阈值。用 9.8 而不是 10.0：ST 股是 5%，科创/创业板是 20%，
# 这里只做情绪的粗略温度，不做精确的涨停统计（那在 limit_up 模块里）。
LIMIT_PCT = 9.8
HISTORY_DAYS = 60          # 页面上画多长的曲线
MA_LONG = 60               # 需要的最长均线，决定要多读多少历史


def build_breadth(store: Any, days: int = HISTORY_DAYS) -> Dict[str, object]:
    """逐日全市场宽度，最近 days 个交易日。数据不足返回空 dict。"""
    from .regime import blend_temp, classify

    conn = store._conn()
    need = days + MA_LONG + 25          # 均线预热 + 新高新低回看
    dates = [str(r[0]) for r in conn.execute(
        "SELECT DISTINCT date FROM daily_kline WHERE amount>0 ORDER BY date DESC LIMIT ?",
        (need,))]
    if len(dates) < MA_LONG + 25:
        return {}
    df = pd.read_sql_query(
        "SELECT date, symbol, close FROM daily_kline WHERE date>=? AND amount>0",
        conn, params=(min(dates),))
    if df.empty:
        return {}

    df = df.sort_values(["symbol", "date"])
    g = df.groupby("symbol", sort=False)["close"]
    df["ret"] = g.pct_change() * 100
    df["ma20"] = g.transform(lambda s: s.rolling(20, min_periods=20).mean())
    df["ma60"] = g.transform(lambda s: s.rolling(60, min_periods=60).mean())
    # 新高/新低看**前** 20 日的极值，shift(1) 排除当日，否则「今天创今天的新高」恒真
    df["hi20"] = g.transform(lambda s: s.rolling(20, min_periods=20).max().shift(1))
    df["lo20"] = g.transform(lambda s: s.rolling(20, min_periods=20).min().shift(1))
    df = df[df["ret"].notna()]

    keep = sorted(dates)[-days:]
    df = df[df["date"].isin(keep)]
    if df.empty:
        return {}

    rows: List[Dict[str, object]] = []
    for date_str, day in df.groupby("date", sort=True):
        total = len(day)
        if total < 100:                 # 半个市场都没有的日子不出数，多半是数据没落全
            continue
        up = int((day["ret"] > 0).sum())
        down = int((day["ret"] < 0).sum())
        # 均线占比的分母只算**算得出均线**的票（次新股没有 60 日线），
        # 拿全市场当分母会让次新股多的时候占比被系统性压低。
        ma20_ok = day["ma20"].notna()
        ma60_ok = day["ma60"].notna()
        hi_ok = day["hi20"].notna()
        rows.append({
            "date": str(date_str),
            "total": total,
            "up": up,
            "down": down,
            "flat": total - up - down,
            "pct_up": round(up / total, 4),
            "above_ma20": round(float((day.loc[ma20_ok, "close"] > day.loc[ma20_ok, "ma20"]).mean()), 4)
            if int(ma20_ok.sum()) else None,
            "above_ma60": round(float((day.loc[ma60_ok, "close"] > day.loc[ma60_ok, "ma60"]).mean()), 4)
            if int(ma60_ok.sum()) else None,
            "new_high20": int((day.loc[hi_ok, "close"] > day.loc[hi_ok, "hi20"]).sum()),
            "new_low20": int((day.loc[hi_ok, "close"] < day.loc[hi_ok, "lo20"]).sum()),
            "limit_up": int((day["ret"] >= LIMIT_PCT).sum()),
            "limit_down": int((day["ret"] <= -LIMIT_PCT).sum()),
            "median_ret": round(float(day["ret"].median()), 3),
        })
    if not rows:
        return {}

    # 环境标签与生产同源：同一天不能出现「这页说偏冷、顶部横幅说中性」
    recent = [(r["median_ret"], r["pct_up"]) for r in rows[-5:]][::-1]
    temp = blend_temp(recent)
    latest = rows[-1]
    return {
        "as_of": latest["date"],
        "temp": round(temp, 1),
        "regime": classify(temp),
        "latest": latest,
        "series": rows,
    }
