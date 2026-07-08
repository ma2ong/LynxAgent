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


from quantcore.quant import arena


PRICES = {"600001": 10.0, "600002": 20.0, "600003": 40.0}
NAMES = {"600001": "甲", "600002": "乙", "600003": "丙"}


def _run(store, monkeypatch, orders_by_persona, llm_ok=True):
    monkeypatch.setattr(arena, "get_local_store", lambda: store)
    monkeypatch.setattr(arena.llm, "available", lambda: llm_ok)
    monkeypatch.setattr(arena, "_ask_persona",
                        lambda persona, *a, **k: orders_by_persona.get(persona["persona"]))
    return arena.run_arena_daily("2026-07-07", ["600001", "600002", "600003"], PRICES, NAMES)


def test_arena_buy_executes_with_cost_and_lot(store, monkeypatch):
    orders = {"价值派": {"sells": [], "buys": [{"symbol": "600001", "weight_pct": 50, "reason": "低估"}],
                        "comment": "买入甲"}}
    result = _run(store, monkeypatch, orders)
    pos = store.arena_positions("价值派")
    # 预算 50 万，成本价 10*1.0003=10.003，可买 49985 股 -> 整手 49900 股
    assert pos[0]["symbol"] == "600001" and pos[0]["shares"] == 49900
    cash = store.arena_cash("价值派")
    assert cash == pytest.approx(1_000_000 - 49900 * 10.0 * 1.0003, abs=1)
    nav = [p for p in result["personas"] if p["persona"] == "价值派"][0]["nav"]
    assert nav == pytest.approx(cash + 49900 * 10.0, abs=1)
    # 其余 4 人格无指令 -> 空仓 NAV=100 万
    assert store.arena_cash("趋势派") == 1_000_000.0


def test_arena_sell_then_buy_and_max_positions(store, monkeypatch):
    store.upsert_arena_position("游资派", "600003", 1000, 30.0)
    store.set_arena_cash("游资派", 100_000.0)
    orders = {"游资派": {"sells": [{"symbol": "600003", "reason": "止盈"}],
                        "buys": [{"symbol": "600002", "weight_pct": 30, "reason": "热点"}],
                        "comment": "换仓"}}
    _run(store, monkeypatch, orders)
    pos = {p["symbol"]: p for p in store.arena_positions("游资派")}
    assert "600003" not in pos          # 已卖出
    assert pos["600002"]["shares"] > 0  # 已买入
    trades = store.load_arena_trades("游资派")
    assert [t["side"] for t in trades] == ["buy", "sell"]  # 最新在前：先卖后买


def test_arena_idempotent_same_day(store, monkeypatch):
    orders = {"价值派": {"sells": [], "buys": [{"symbol": "600001", "weight_pct": 50, "reason": "x"}],
                        "comment": ""}}
    _run(store, monkeypatch, orders)
    shares1 = store.arena_positions("价值派")[0]["shares"]
    _run(store, monkeypatch, orders)  # 同日重跑：全员已有 NAV，直接跳过
    assert store.arena_positions("价值派")[0]["shares"] == shares1
    assert len(store.load_arena_trades("价值派")) == 1


def test_arena_llm_unavailable_settles_nav_without_trading(store, monkeypatch):
    store.upsert_arena_position("价值派", "600001", 1000, 9.0)
    store.set_arena_cash("价值派", 500_000.0)
    result = _run(store, monkeypatch, {}, llm_ok=False)
    assert store.load_arena_trades("价值派") == []
    nav = [p for p in result["personas"] if p["persona"] == "价值派"][0]["nav"]
    assert nav == pytest.approx(500_000 + 1000 * 10.0)
    assert store.arena_nav_exists("2026-07-07", "价值派")


def test_arena_ignores_symbols_outside_candidates_and_holdings(store, monkeypatch):
    orders = {"价值派": {"sells": [], "buys": [{"symbol": "999999", "weight_pct": 50, "reason": "幻觉"}],
                        "comment": ""}}
    _run(store, monkeypatch, orders)
    assert store.arena_positions("价值派") == []  # 幻觉代码被过滤
