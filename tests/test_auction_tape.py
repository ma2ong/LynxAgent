"""集合竞价四形态识别：主力抢筹/诱多出货/洗盘低吸/多空分歧。"""
from quantcore.quant.auction_tape import (
    classify_trajectory,
    record_sample,
    reset_if_new_day,
    tape_summary,
)


def _traj(prices, amounts=None):
    """(hhmmss, price, cumulative_amount) 序列；量能默认线性放大。"""
    n = len(prices)
    amounts = amounts if amounts is not None else [1e6 * (i + 1) for i in range(n)]
    return [(f"09{15 + i:02d}00", p, a) for i, (p, a) in enumerate(zip(prices, amounts))]


def test_insufficient_samples():
    res = classify_trajectory(_traj([10.0, 10.1]), 10.0)
    assert res["pattern"] == "insufficient"


def test_accumulation():
    """临时价一路走高 + 量能放大 → 主力抢筹。"""
    prices = [10.1, 10.2, 10.35, 10.5, 10.6]
    amounts = [1e6, 3e6, 6e6, 1.0e7, 1.5e7]  # 后段增量更大
    res = classify_trajectory(_traj(prices, amounts), 10.0)
    assert res["pattern"] == "accumulation"
    assert res["drift"] > 0 and res["gap_last"] > 0


def test_distribution_bull_trap():
    """明显高开(+3%)后临时价一路走低 → 诱多出货。"""
    prices = [10.3, 10.2, 10.1, 10.0, 9.95]
    res = classify_trajectory(_traj(prices), 10.0)
    assert res["pattern"] == "distribution"
    assert res["gap_open"] >= 2.0 and res["drift"] < 0


def test_shakeout():
    """低开/平开后探底回升 → 洗盘低吸。"""
    prices = [10.0, 9.9, 9.85, 10.1, 10.3]
    res = classify_trajectory(_traj(prices), 10.0)
    assert res["pattern"] == "shakeout"
    assert res["drift"] >= 0.8


def test_divergence_choppy():
    """价格反复上下拉锯 → 多空分歧。"""
    prices = [10.2, 10.0, 10.25, 10.0, 10.2, 10.0]
    res = classify_trajectory(_traj(prices), 10.0)
    assert res["pattern"] == "divergence"
    assert res["reversals"] >= 3


def test_record_and_summary():
    """采样器只跟踪异动票（|高开|≥1%），并能汇总形态计数。"""
    reset_if_new_day("2099-01-01")
    snap = {
        "600000": {"code": "600000", "price": 10.5, "prev_close": 10.0, "amount": 1e6},  # +5% 纳入
        "600001": {"code": "600001", "price": 10.02, "prev_close": 10.0, "amount": 1e6},  # +0.2% 不纳入
    }
    for _ in range(4):
        record_sample(snap)  # 同价重复采样（仅验证纳入/排除逻辑）
    summary = tape_summary()
    assert "600000" in summary["per_symbol"]
    assert "600001" not in summary["per_symbol"]
    assert summary["tracked"] == 1
