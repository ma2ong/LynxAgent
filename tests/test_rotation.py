"""板块轮动（RRG）坐标的性质测试。

这里不测「某个板块该落在哪一格」——那取决于行情，测了也只是把实现抄一遍。
测的是两条**性质**：坐标必须能把强弱分开，且不能依赖取了多少历史。
"""
from datetime import date, timedelta

import pandas as pd
import pytest

from quantcore.quant.local_store import LocalQuantStore
from quantcore.quant.rotation import build_rotation


@pytest.fixture()
def store(tmp_path):
    return LocalQuantStore(str(tmp_path / "test.sqlite"))


def _trading_dates(n: int):
    out = []
    d = date.today() - timedelta(days=n * 2 + 5)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


def _seed(store, symbol, closes):
    n = len(closes)
    df = pd.DataFrame({
        "date": _trading_dates(n), "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": [1e6] * n, "amount": [1e8] * n,
    })
    store.upsert_kline(symbol, df)


# 横截面标准化至少要 5 个板块才有意义（两个点的 z 值恒为 ±1σ），所以最少造 6 个，
# 日涨幅按梯度铺开：第 0 个最强、第 5 个最弱，正好用来验证「强的排右边」。
SECTORS = 6


def _seed_sectors(store, monkeypatch, n=150):
    """造 6 个强弱成梯度的板块，每个 6 只成分股（过 MIN_MEMBERS）。

    后 40 天给每个板块换一次斜率（强的更强、弱的更弱），否则日涨幅恒定 → 累计超额
    是条直线 → 它的 20 日变化恒为 0，纵轴就完全没被测到（还会撞上标准化的零离散度
    分支）。轮动图的重点恰恰是纵轴，测试数据必须让它真的动起来。
    """
    mapping = {}
    for s in range(SECTORS):
        daily = 0.004 - s * 0.0016          # +0.4%/天 → −0.4%/天
        later = daily * 2.5                 # 后段拉开差距，让强度变化不为零
        closes = [10.0]
        for i in range(n - 1):
            closes.append(closes[-1] * (1 + (later if i >= n - 40 else daily)))
        for i in range(6):
            sym = f"{60 + s}{i:04d}"
            _seed(store, sym, closes)
            mapping[sym] = f"板块{s}"
    monkeypatch.setattr("quantcore.quant.industry.industry_map", lambda: mapping)
    return mapping


def test_relative_strength_follows_actual_strength(store, monkeypatch):
    """强弱梯度必须原样体现在横轴上：板块0 最强、板块5 最弱，rs_ratio 应单调递减。"""
    _seed_sectors(store, monkeypatch)
    by = {it["industry"]: it for it in build_rotation(store)["items"]}
    assert len(by) == SECTORS
    ratios = [by[f"板块{s}"]["rs_ratio"] for s in range(SECTORS)]
    assert ratios == sorted(ratios, reverse=True), ratios
    assert by["板块0"]["mom20"] > 0 > by["板块5"]["mom20"]
    # 最强的那个必须落在 sector_hot 档（前 20% 分位），这是审计认可的那一档
    assert by["板块0"]["sector_hot"]


def test_coordinates_do_not_depend_on_lookback(store, monkeypatch):
    """同一天的坐标不能因为多读了历史就变。

    这是 2026-08-31 修掉的一个真缺陷的回归测试：相对强度原本用「从窗口第一天起的
    累乘比值」，锚点就是窗口边缘，于是把 lookback 从 125 调到 160，板块会整体平移、
    象限跟着翻。改成滚动窗口内的累计超额之后才与锚点无关。拿旧实现跑，下面两个
    断言会直接失败——这正是这条测试存在的意义。
    """
    _seed_sectors(store, monkeypatch, n=200)
    short = {it["industry"]: it for it in build_rotation(store, lookback=125)["items"]}
    long = {it["industry"]: it for it in build_rotation(store, lookback=180)["items"]}
    assert set(short) == set(long)
    for name in short:
        assert short[name]["rs_ratio"] == pytest.approx(long[name]["rs_ratio"], abs=1e-6)
        assert short[name]["rs_momentum"] == pytest.approx(long[name]["rs_momentum"], abs=1e-6)
        assert short[name]["quadrant"] == long[name]["quadrant"]


def test_returns_empty_when_history_too_short(store, monkeypatch):
    """样本不够就返回空 dict，让端点如实说明，而不是画一张没有意义的坐标系。"""
    _seed_sectors(store, monkeypatch, n=40)
    assert build_rotation(store) == {}


def test_leaders_backfill_to_a_full_row(store, monkeypatch):
    """够流动性的不足一行时，用板块内成交额最大的剩余个股补满。

    窄板块（酒店餐饮、房地产服务这类）里日均成交额过 1 亿的常常只有三四只，不补的话
    卡片会被拉成超宽的一条，和别的板块参差不齐。补位按成交额取——补的目的是让这一行
    是完整的板块面貌，不是硬凑几只涨得好的。
    """
    from quantcore.quant.rotation import LEADERS_PER_SECTOR, build_rotation as _b

    mapping = _seed_sectors(store, monkeypatch)
    # 再塞一个只有 2 只「够流动性」、其余都是小额票的窄板块
    n = 150
    closes = [10.0]
    for _ in range(n - 1):
        closes.append(closes[-1] * 1.003)
    for i in range(7):
        sym = f"9000{i:02d}"
        df = pd.DataFrame({
            "date": _trading_dates(n), "open": closes, "high": closes, "low": closes,
            "close": closes, "volume": [1e6] * n,
            # 前两只 5 亿，其余 5000 万（过补位下限 3000 万、不过正选下限 1 亿）
            "amount": [5e8 if i < 2 else 5e7] * n,
        })
        store.upsert_kline(sym, df)
        mapping[sym] = "窄板块"
    monkeypatch.setattr("quantcore.quant.industry.industry_map", lambda: mapping)

    by = {it["industry"]: it for it in _b(store)["items"]}
    leaders = by["窄板块"]["leaders"]
    assert len(leaders) == min(LEADERS_PER_SECTOR, 7)
    # 正选（≥1 亿）排在前面
    assert leaders[0]["amount"] >= 1.0 and leaders[1]["amount"] >= 1.0


def test_st_names_never_appear_as_leaders(store, monkeypatch):
    """ST / 退市整理股不能出现在龙头卡片上。

    补位是按成交额取的，不排掉的话窄板块会顶上来一只 *ST（实测房地产服务补进过
    *ST皇庭）。集合竞价本来就把 ST 排除在候选之外，这里必须一致。
    """
    from quantcore.quant.rotation import build_rotation as _b

    mapping = _seed_sectors(store, monkeypatch)
    n = 150
    closes = [10.0]
    for _ in range(n - 1):
        closes.append(closes[-1] * 1.01)          # 涨得最凶，不排除的话必进正选
    store.upsert_kline("900999", pd.DataFrame({
        "date": _trading_dates(n), "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": [1e6] * n, "amount": [9e8] * n,
    }))
    mapping["900999"] = "板块0"
    store.upsert_meta([{"symbol": "900999", "name": "*ST测试"}])
    monkeypatch.setattr("quantcore.quant.industry.industry_map", lambda: mapping)

    names = {l["name"] for it in _b(store)["items"] for l in it["leaders"]}
    assert not any("ST" in n.upper() for n in names)
