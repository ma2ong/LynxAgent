"""Arena 虚拟盘（仓储 + 交易执行 + 结算）回归测试。"""
import pytest

from quantcore.quant.local_store import LocalQuantStore


@pytest.fixture()
def store(tmp_path):
    return LocalQuantStore(str(tmp_path / "test.sqlite"))


def test_arena_cash_defaults_to_one_million(store):
    assert store.arena_cash("价值派") == 1_000_000.0
    store.set_arena_cash("价值派", 500_000.0)
    assert store.arena_cash("价值派") == 500_000.0
    assert store.arena_cash("趋势派") == 1_000_000.0  # 互不影响


def test_arena_positions_roundtrip(store):
    assert store.arena_positions("价值派") == []
    store.upsert_arena_position("价值派", "600001", 1000, 10.5)
    store.upsert_arena_position("价值派", "600001", 1500, 11.0)  # 覆盖
    pos = store.arena_positions("价值派")
    assert pos == [{"symbol": "600001", "shares": 1500, "avg_cost": 11.0}]
    store.delete_arena_position("价值派", "600001")
    assert store.arena_positions("价值派") == []


def test_arena_trades_log(store):
    store.insert_arena_trade("2026-07-07", "价值派", "600001", "buy", 10.0, 1000, "低估买入")
    store.insert_arena_trade("2026-07-07", "价值派", "600001", "sell", 11.0, 1000, "止盈")
    trades = store.load_arena_trades("价值派", limit=10)
    assert len(trades) == 2
    assert trades[0]["side"] == "sell"  # 最新在前
    assert trades[0]["reason"] == "止盈"


def test_arena_nav_roundtrip_and_series(store):
    assert not store.arena_nav_exists("2026-07-07", "价值派")
    store.save_arena_nav("2026-07-07", "价值派", 1_010_000.0, 200_000.0, "看多科技")
    store.save_arena_nav("2026-07-08", "价值派", 1_020_000.0, 150_000.0, "")
    store.save_arena_nav("2026-07-07", "趋势派", 990_000.0, 990_000.0, "")
    assert store.arena_nav_exists("2026-07-07", "价值派")
    series = store.load_arena_nav_series()
    assert series["价值派"] == [
        {"date": "2026-07-07", "nav": 1_010_000.0, "comment": "看多科技"},
        {"date": "2026-07-08", "nav": 1_020_000.0, "comment": ""},
    ]
    assert len(series["趋势派"]) == 1
