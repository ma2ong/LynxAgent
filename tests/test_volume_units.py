"""成交量单位：全库统一按「手」存，成交额由它推导。

回归背景（2026-07-28）：腾讯对科创板（688）按**股**给成交量，其余板块按**手**，
而 sync 一律当成手再乘 100 算成交额，科创板的量与额因此被放大 100 倍——全市场
成交额能算出 32 万亿，而 A 股实际约 1.5~2 万亿。这个错误很隐蔽：数字看着有量纲、
排序也正常，只有跟别的板块横向比才露馅，而它会让科创板在所有以成交额为门槛的
地方（选股候选、liquidity 因子、热力图）被系统性高估。
"""
import pytest

from quantcore.quant.sync_service import _volume_to_lots


@pytest.mark.parametrize("symbol", ["600519", "000001", "300750", "002415", "601318", "301321"])
def test_non_star_volume_passes_through(symbol):
    """主板/创业板数据源已经按手给量，不得再折算。"""
    assert _volume_to_lots(symbol, 12345.0) == 12345.0


@pytest.mark.parametrize("symbol", ["688347", "688008", "688256"])
def test_star_board_volume_converted_to_lots(symbol):
    """科创板按股给量，必须折算成手，否则成交额会放大 100 倍。"""
    assert _volume_to_lots(symbol, 1_234_500.0) == 12_345.0


def test_amount_invariant_holds_for_both_boards():
    """折算后，`close × 手 × 100` 对两类板块都给出正确的成交额（元）。

    构造：两只股票当日都真实成交了 100 万股、股价 10 元 → 成交额都应是 1000 万元。
    数据源对主板报 10000 手，对科创板报 1000000 股。
    """
    close = 10.0
    main_lots = _volume_to_lots("600519", 10_000.0)      # 源已是手
    star_lots = _volume_to_lots("688347", 1_000_000.0)   # 源是股
    assert main_lots == star_lots == 10_000.0
    assert close * main_lots * 100 == close * star_lots * 100 == 10_000_000.0
