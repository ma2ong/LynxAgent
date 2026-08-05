"""盘中雷达的板块强弱必须能区分「很热」和「有点热」。

线上实况（2026-08-05 11:02）：贵金属板块 +6.02%，软件开发 +2.26%，而雷达前四名里
三只是软件开发，贵金属那只反而排在后面。根因是板块项用绝对涨幅且被 clip 在 ±18：

    context_score = 48 + _clip(sector_mean * 6, -18, 18)   # 涨到 3% 就封顶

板块涨 3% 与涨 6% 拿完全相同的分，再乘 0.20 权重，最终分只差不到 1 分 —— 板块共振
形同虚设。改成全行业横截面分位后，"今天最强的板块"总能被排出来。
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from quantcore.quant.intraday_signals import IntradaySignalEngine

TZ = ZoneInfo("Asia/Shanghai")

# 复刻截图当时的板块格局
SECTORS = {
    "贵金属": 6.02,
    "小金属": 5.72,
    "半导体": 4.55,
    "软件开发": 2.26,
    "银行": 0.20,
    "公用事业": -1.10,
}
FILLERS_PER_SECTOR = 9


def _base_row(name: str, industry: str) -> dict:
    return {
        "name": name, "industry": industry, "prev_close": 10.0,
        "ma5": 10.0, "ma20": 9.8, "ma60": 9.5,
        "high20": 10.4, "high60": 10.8,
        "amount_ma20": 100_000_000, "return20": 8.0,
    }


def _quote_row(symbol: str, name: str, pct: float, volume_ratio: float) -> dict:
    price = 10.0 * (1 + pct / 100)
    return {
        "symbol": symbol, "name": name, "price": price, "prev_close": 10.0,
        "open": 10.02, "high": price + 0.01, "low": 10.0,
        "pct_chg": pct, "amount": 30_000_000, "volume_ratio": volume_ratio,
        "quote_source": "test",
    }


def _market() -> tuple[dict, dict]:
    """六个板块各 9 只陪跑票，外加两只除板块外完全相同的主角。"""
    baselines, quotes = {}, {}
    idx = 0
    for industry, sector_pct in SECTORS.items():
        for _ in range(FILLERS_PER_SECTOR):
            sym = str(idx).zfill(6)
            baselines[sym] = _base_row(f"陪跑{idx}", industry)
            quotes[sym] = _quote_row(sym, f"陪跑{idx}", sector_pct, 1.0)
            idx += 1
    # 两只主角：量价、结构、涨幅全部一致，只有所属板块不同
    for sym, industry in (("900001", "贵金属"), ("900002", "软件开发")):
        baselines[sym] = _base_row(f"主角{sym}", industry)
        quotes[sym] = _quote_row(sym, f"主角{sym}", 5.0, 3.2)
    return baselines, quotes


def _scores(baselines, quotes) -> dict:
    engine = IntradaySignalEngine(baselines=baselines)
    result = engine.scan(quotes, datetime(2026, 8, 5, 11, 2, tzinfo=TZ))
    return {item["symbol"]: item["score"] for item in result["items"]}


def test_hot_sector_stock_outranks_lukewarm_sector_twin():
    """同样的量价结构，身处 +6% 板块的必须排在 +2.26% 板块的前面，且差距要看得见。"""
    scores = _scores(*_market())
    hot, mild = scores["900001"], scores["900002"]
    assert hot > mild, f"贵金属 {hot} 应高于软件开发 {mild}"
    # 旧口径下这个差距只有约 0.9 分，会被个股层的任何微小差异淹没
    assert hot - mild >= 1.5, f"板块差距仍然太小：{hot - mild:.2f} 分"


def test_sector_strength_does_not_saturate():
    """把最热板块从 +6% 拉到 +12%，分差必须继续拉开 —— 这正是旧口径做不到的。"""
    baselines, quotes = _market()
    gap_at_6 = _scores(baselines, quotes)
    base_gap = gap_at_6["900001"] - gap_at_6["900002"]

    for sym, q in quotes.items():
        if baselines[sym]["industry"] == "贵金属" and sym != "900001":
            q.update(_quote_row(sym, q["name"], 12.0, 1.0))
    hotter = _scores(baselines, quotes)
    assert hotter["900001"] - hotter["900002"] >= base_gap


def test_thin_sector_cannot_hijack_the_ranking():
    """只有一两只成分的板块不参与排序 —— 一只涨停票不等于一个板块在爆发。"""
    baselines, quotes = _market()
    baselines["900003"] = _base_row("独苗", "冷门独苗板块")
    quotes["900003"] = _quote_row("900003", "独苗", 9.9, 3.2)
    scores = _scores(baselines, quotes)
    # 独苗票按中性板块处理，不能因为"它自己的板块涨 9.9%"就压过真热板块里的主角
    assert scores["900001"] > scores["900003"]
