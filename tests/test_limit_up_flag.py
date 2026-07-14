"""涨停标注：按板块判定当前是否封在涨停（回放显示形态池 43% 入选票当日已涨停，
展示的收盘买入价实际买不到，必须让用户看见）。"""
from quantcore.quant.engine import board_limit_pct, is_limit_up


def test_board_limit_by_board():
    assert board_limit_pct("600000") == 10.0   # 主板
    assert board_limit_pct("300750") == 20.0   # 创业板
    assert board_limit_pct("688981") == 20.0   # 科创板
    assert board_limit_pct("920123") == 30.0   # 北交所


def test_is_limit_up_respects_board():
    # 主板 10cm：9.9% 算涨停（含四舍五入后的封板价），9.0% 不算
    assert is_limit_up("600000", 9.9)
    assert is_limit_up("600000", 10.0)
    assert not is_limit_up("600000", 9.0)
    # 创业板 20cm：10% 只是普通大涨，19.8% 才是涨停
    assert not is_limit_up("300750", 10.0)
    assert is_limit_up("300750", 19.8)
    # 北交所 30cm
    assert not is_limit_up("920123", 20.0)
    assert is_limit_up("920123", 29.5)


def test_is_limit_up_handles_bad_input():
    assert not is_limit_up("600000", None)
    assert not is_limit_up("600000", float("nan"))
