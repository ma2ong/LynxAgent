"""涨跌停阈值：板块上限统一走 board_limit_pct；ST 与所在板块同幅度。"""
from quantcore.quant.market_sentiment import _limit_threshold


def test_board_limits():
    assert abs(_limit_threshold("600001") - 0.097) < 1e-9
    assert abs(_limit_threshold("300001") - 0.194) < 1e-9
    assert abs(_limit_threshold("688001") - 0.194) < 1e-9
    # 北交所 30%：涨 10% 不能算涨停（旧实现按 9.7% 判，会误算）
    assert _limit_threshold("920001") > 0.29
    assert _limit_threshold("830001") > 0.29


def test_st_follows_board_limit():
    """ST 按新规与所在板块同幅度（主板 10%），不再单独按 5%。"""
    assert abs(_limit_threshold("600001") - 0.097) < 1e-9
