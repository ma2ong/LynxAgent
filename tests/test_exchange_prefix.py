"""交易所前缀映射：北交所 920 段不能被判给沪市。

回归背景（2026-07-28）：`9` 开头原本是给沪市 B 股（900xxx）写的规则，但北交所 2023 年
起启用了 920xxx 代码段，于是 317 只北交所个股被映射成 sh920xxx，腾讯查无此码 →
日线停在某一天不再增长、实时报价也永远拿不到。整个过程不报任何错，界面上只表现为
「今日 5208/5525 永远补不满」，靠肉眼极难定位。

同一份映射在四个模块里各写了一遍（行情快照、引擎、同步、baostock），四处都错。
这个测试对四处同时把关，避免将来只修一处。
"""
import pytest

from app.core.market_data import _market_quote_code
from quantcore.quant.data_sources import _baostock_code
from quantcore.quant.engine import _market_quote_code as engine_code
from quantcore.quant.sync_service import _tencent_code

MAPPERS = [
    pytest.param(_tencent_code, "bj", "sh", "sz", id="sync_service"),
    pytest.param(_market_quote_code, "bj", "sh", "sz", id="market_data"),
    pytest.param(engine_code, "bj", "sh", "sz", id="engine"),
]


@pytest.mark.parametrize("fn,bj,sh,sz", MAPPERS)
@pytest.mark.parametrize("symbol", ["920000", "920001", "920914", "920685"])
def test_beijing_new_code_range_maps_to_bj(fn, bj, sh, sz, symbol):
    """920 段是北交所，不是沪市——判错就是 317 只个股静默停更。"""
    assert fn(symbol) == f"{bj}{symbol}"


@pytest.mark.parametrize("fn,bj,sh,sz", MAPPERS)
@pytest.mark.parametrize("symbol", ["830799", "430139", "870508", "880123"])
def test_beijing_legacy_code_range_still_maps_to_bj(fn, bj, sh, sz, symbol):
    assert fn(symbol) == f"{bj}{symbol}"


@pytest.mark.parametrize("fn,bj,sh,sz", MAPPERS)
@pytest.mark.parametrize("symbol", ["600519", "601318", "603986", "900901", "900957"])
def test_shanghai_including_b_shares(fn, bj, sh, sz, symbol):
    """900xxx 是沪市 B 股，修 920 时不能把它一起带偏。"""
    assert fn(symbol) == f"{sh}{symbol}"


@pytest.mark.parametrize("fn,bj,sh,sz", MAPPERS)
@pytest.mark.parametrize("symbol", ["000001", "002415", "300750", "301321"])
def test_shenzhen(fn, bj, sh, sz, symbol):
    assert fn(symbol) == f"{sz}{symbol}"


def test_baostock_uses_dotted_prefixes():
    assert _baostock_code("920000") == "bj.920000"
    assert _baostock_code("830799") == "bj.830799"
    assert _baostock_code("600519") == "sh.600519"
    assert _baostock_code("900901") == "sh.900901"
    assert _baostock_code("000001") == "sz.000001"
