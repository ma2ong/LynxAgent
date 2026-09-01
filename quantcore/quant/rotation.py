"""板块相对轮动（RRG）：把「板块相对强度」和「它在变强还是变弱」放到同一张图上。

为什么是这张图
--------------
系统里唯一通过全部审计闸门的信号是 20 日板块动量（sector_hot：板块 20 日强度前 20%
的票，匹配对照增量为正且跨年稳定）。但产品侧此前只有一张热力图，答的是「现在谁涨得多」
——那是**当日**涨跌幅，跟被验证的那个量根本不是一回事。这个模块把被验证的量本身做成
可读的形态：横轴看板块中期（60 日）相对全市场是强是弱，纵轴看近期（20 日）强不强，于是板块会
沿「改善 → 领先 → 走弱 → 落后」四象限转圈，轨迹尾巴显示它最近几周走到哪一步。

基准口径
--------
基准是**全市场个股日收益中位数**累乘，与 experiments/rule_audit.py 的超额基准、
以及回放的 excess 口径同源。不用指数：指数被权重股主导，而选股系统买的是个股，
拿指数当基准会把「大票涨小票跌」的日子误判成普涨。

坐标是相对量，不是绝对涨幅
--------------------------
rs_ratio 是「近 60 日累计跑赢全市场中位多少」，rs_mom 是「近 20 日累计跑赢多少」；
两者都对**当期全部板块**做横截面标准化后再平移到 100 附近。
普涨普跌日里所有板块的绝对涨幅一起动，但相对位置不动 —— 这正是想要的：轮动图问的是
「资金在往哪挪」，不是「今天涨没涨」。因此这两个数不能当收益读。

mom20 / mom20_pct 才是可以直接对照审计结论的那两个数。注意 **mom20 是板块近 20 日的
累计涨幅（成分股日涨幅中位数求和），不是超额** —— 没有减全市场基准。这样才和审计里
sector_hot 的口径一致（那条规则用的就是原始 20 日动量的横截面分位）。界面上必须写成
「20 日涨幅」，写成「超额」会和 rs_ratio/rs_mom 那两个真·超额量混淆。
mom20_pct 是它在当日全部板块里的分位，≥0.8 即 sector_hot 命中的那一档。
"""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

# 成分不足这个数的板块不参与排序：三只票的中位数不代表一个板块
MIN_MEMBERS = 5
# 标准化窗口。60 个交易日≈一个季度，短于此则 z 值会被单周行情主导
NORM_WINDOW = 60
# 动量窗口。20 日是审计里唯一稳的那个跨度，不要随手改成 5 或 10
MOM_WINDOW = 20
# 轨迹尾巴：取多少个采样点、每点间隔多少个交易日
TAIL_POINTS = 8
TAIL_STEP = 5


def _quadrant(rs: float, mom: float) -> str:
    if rs >= 100 and mom >= 100:
        return "领先"
    if rs >= 100:
        return "走弱"
    if mom >= 100:
        return "改善"
    return "落后"


# 横截面离散度低于这个值就当作"全板块没有分化"。单位与输入一致（百分点），
# 而板块间的累计超额差异正常在个位数以上，所以 1e-9 只可能是浮点残差。
MIN_DISPERSION = 1e-9


def _z_to_axis(frame: pd.DataFrame) -> pd.DataFrame:
    """逐日对全部板块做横截面标准化，平移到 100 附近（1σ = 5 个刻度）。

    离散度过小的那些天整行给 100（正中，无倾向），不做标准化。否则当所有板块的
    强度变化几乎一样时，除以一个约等于 0 的标准差会把 1e-15 级的浮点残差放大成
    满量程坐标 —— 图上会出现一批看着很笃定、其实纯属数值噪声的象限归属。
    """
    mean = frame.mean(axis=1)
    std = frame.std(axis=1)
    scaled = 100 + 5 * frame.sub(mean, axis=0).div(std.where(std > MIN_DISPERSION), axis=0)
    return scaled.fillna(100.0).where(frame.notna())


def build_rotation(store: Any, lookback: int = 125) -> Dict[str, object]:
    """算出每个板块的 RRG 坐标与最近若干周的轨迹。

    lookback 是取多少个交易日的日线。需要 NORM_WINDOW + MOM_WINDOW + 轨迹跨度
    （60+20+35=115），默认 125 只留必要余量 —— 每多取 10 天就多读约 5 万行，
    而这段读库是整个函数最贵的一步。数据不够时返回空 dict，调用方据此决定是否展示。
    """
    from .industry import industry_map

    mapping = industry_map()
    if not mapping:
        return {}

    conn = store._conn()
    dates = [str(r[0]) for r in conn.execute(
        "SELECT DISTINCT date FROM daily_kline WHERE amount>0 ORDER BY date DESC LIMIT ?",
        (lookback,))]
    if len(dates) < NORM_WINDOW + MOM_WINDOW + 5:
        return {}
    since = min(dates)
    df = pd.read_sql_query(
        "SELECT date, symbol, close FROM daily_kline WHERE date>=? AND amount>0",
        conn, params=(since,))
    if df.empty:
        return {}

    df["industry"] = df["symbol"].map(mapping)
    df = df[df["industry"].notna()]
    df = df.sort_values(["symbol", "date"])
    df["ret"] = df.groupby("symbol", sort=False)["close"].pct_change() * 100
    df = df[df["ret"].notna()]
    if df.empty:
        return {}

    # 板块日收益 = 成分股当日涨幅中位数（中位而非均值：一只涨停票不能代表板块）
    grp = df.groupby(["date", "industry"], sort=False)["ret"]
    sec = grp.median().rename("ret").reset_index()
    sec["n"] = grp.size().to_numpy()
    sec = sec[sec["n"] >= MIN_MEMBERS]
    if sec.empty:
        return {}
    bench = df.groupby("date")["ret"].median()

    wide = sec.pivot(index="date", columns="industry", values="ret").sort_index()
    # 成分覆盖不全的板块（中途才够 5 只）会留下 NaN，直接剔掉整列，别用 0 填充——
    # 用 0 填充等于凭空给它记了一天"不涨不跌"，会把它的相对强度往中间拖。
    wide = wide.dropna(axis=1)
    if wide.shape[1] < 5 or len(wide) < NORM_WINDOW + MOM_WINDOW:
        return {}

    # 相对强度用**滚动窗口内的累计超额**，不用「从窗口第一天起的累乘比值」。
    # 后者看着更像教科书里的 RS-Ratio，但它的锚点就是窗口边缘：多取或少取几十天历史，
    # 每个板块的 RS 水平会整体平移，象限跟着翻——一张换个参数就变结论的图不能上产品。
    # 滚动口径是 point-in-time 的：每一天只回看固定的 NORM_WINDOW 天，与锚点无关。
    excess = wide.sub(bench.reindex(wide.index), axis=0)

    # 横轴 = 近 60 日累计超额，纵轴 = 近 20 日累计超额。两个窗口，一个中期一个近期。
    #
    # 纵轴曾经用「60 日累计超额的 20 日变化」（教科书 RRG 的 RS-Momentum 就是这么定义的），
    # 但那个量等于「近 20 日超额 − 刚滚出窗口的那 20 日超额」，带一个窗口滚出假象：
    # 60~80 天前暴涨过的板块，哪怕最近 20 天很强，差值照样是负的。结果是图上落在「落后」
    # 象限、右侧列表却显示 20 日涨幅 99 分位 —— 两处互相打脸，用户只会当成 bug。
    # 换成直接用 20 日累计超额后，纵轴与列表里的 mom20 同源，象限读数与数字一致。
    # 代价是两轴有窗口重叠（20 日包含在 60 日里）因而相关，这是可接受的：轮动周期
    # 「改善（近期强、中期弱）→ 领先（都强）→ 走弱（中期强、近期弱）→ 落后（都弱）」
    # 反而比原口径更贴合。
    rs = excess.rolling(NORM_WINDOW).sum()
    rs_ratio = _z_to_axis(rs)
    rs_mom = _z_to_axis(excess.rolling(MOM_WINDOW).sum())

    # 与审计口径完全一致的那个数：板块 20 日累计中位涨幅的横截面分位
    mom20 = wide.rolling(MOM_WINDOW).sum()
    mom20_pct = mom20.rank(axis=1, pct=True)

    idx = list(rs_ratio.index)
    tail_idx = [idx[-1 - i * TAIL_STEP] for i in range(TAIL_POINTS)
                if len(idx) > 1 + i * TAIL_STEP][::-1]
    last = idx[-1]

    # 成分数按板块取一次，别在下面的循环里对整张 sec 反复做布尔筛选
    members = sec[sec["date"] == last].set_index("industry")["n"].to_dict()

    items: List[Dict[str, object]] = []
    for name in wide.columns:
        x = float(rs_ratio.at[last, name])
        y = float(rs_mom.at[last, name])
        if x != x or y != y:
            continue
        items.append({
            "industry": str(name),
            "rs_ratio": round(x, 2),
            "rs_momentum": round(y, 2),
            "quadrant": _quadrant(x, y),
            "mom20": round(float(mom20.at[last, name]), 2),
            "mom20_pct": round(float(mom20_pct.at[last, name]), 3),
            "sector_hot": bool(mom20_pct.at[last, name] >= 0.8),
            "members": int(members.get(name, 0)),
            "tail": [
                {"date": str(d), "x": round(float(rs_ratio.at[d, name]), 2),
                 "y": round(float(rs_mom.at[d, name]), 2)}
                for d in tail_idx
                if rs_ratio.at[d, name] == rs_ratio.at[d, name]
                and rs_mom.at[d, name] == rs_mom.at[d, name]
            ],
        })
    items.sort(key=lambda it: it["mom20_pct"], reverse=True)
    return {
        "as_of": str(last),
        "benchmark": "全市场个股日收益中位数",
        "mom_window": MOM_WINDOW,
        "norm_window": NORM_WINDOW,
        "items": items,
    }
