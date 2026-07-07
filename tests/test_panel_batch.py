"""五方判读批量化（panel_scores 表 + panel_batch 逻辑）回归测试。"""
import pytest

from quantcore.quant.local_store import LocalQuantStore


@pytest.fixture()
def store(tmp_path):
    return LocalQuantStore(str(tmp_path / "test.sqlite"))


def _payload(consensus=60.0, divergence=20.0):
    return {
        "consensus_score": consensus, "divergence": divergence,
        "bull_count": 3, "bear_count": 1, "summary": "共识偏多",
        "verdicts": [{"persona": "价值派", "style": "value", "score": 70,
                      "stance": "看多", "reason": "低估"}],
    }


def test_panel_score_roundtrip(store):
    store.save_panel_score("2026-07-07", "600001", _payload())
    scores = store.load_panel_scores("2026-07-07", ["600001", "600002"])
    assert set(scores.keys()) == {"600001"}
    assert scores["600001"]["consensus_score"] == 60.0
    assert scores["600001"]["verdicts"][0]["persona"] == "价值派"
    # 同日同股重存覆盖
    store.save_panel_score("2026-07-07", "600001", _payload(consensus=80.0))
    assert store.load_panel_scores("2026-07-07", ["600001"])["600001"]["consensus_score"] == 80.0


def test_panel_scores_scoped_by_date(store):
    store.save_panel_score("2026-07-04", "600001", _payload())
    assert store.load_panel_scores("2026-07-07", ["600001"]) == {}


def test_load_panel_scores_no_symbols_returns_all_of_day(store):
    store.save_panel_score("2026-07-07", "600001", _payload())
    store.save_panel_score("2026-07-07", "600002", _payload(consensus=40.0))
    scores = store.load_panel_scores("2026-07-07")
    assert len(scores) == 2


def test_load_picks_symbols(store):
    store.record_picks("smart", [
        {"symbol": "600001", "name": "甲", "score": 90, "close": 10.0},
        {"symbol": "600002", "name": "乙", "score": 80, "close": 20.0},
    ])
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")
    assert store.load_picks_symbols(today, "smart", limit=10) == ["600001", "600002"]
    assert store.load_picks_symbols(today, "pattern") == []
    assert store.load_picks_symbols(today, "smart", limit=1) == ["600001"]


from quantcore.quant import investor_panel as ip


def test_run_panel_batch_scores_missing_and_skips_scored(store, monkeypatch):
    calls: list[str] = []

    def fake_panel(symbol):
        calls.append(symbol)
        return {"empty": False, "symbol": symbol, "consensus_score": 66.0,
                "divergence": 10.0, "bull_count": 4, "bear_count": 0,
                "verdicts": [], "summary": "ok"}

    monkeypatch.setattr(ip, "investor_panel", fake_panel)
    monkeypatch.setattr(ip, "get_local_store", lambda: store)
    store.save_panel_score("2026-07-07", "600001", {"consensus_score": 50})

    n = ip.run_panel_batch("2026-07-07", ["600001", "600002", "600003"])
    assert n == 2  # 600001 已有评分被跳过
    assert calls == ["600002", "600003"]
    assert set(store.load_panel_scores("2026-07-07").keys()) == {"600001", "600002", "600003"}


def test_run_panel_batch_skips_failed_scores(store, monkeypatch):
    monkeypatch.setattr(ip, "investor_panel",
                        lambda s: {"empty": True, "message": "no llm"})
    monkeypatch.setattr(ip, "get_local_store", lambda: store)
    assert ip.run_panel_batch("2026-07-07", ["600001"]) == 0
    assert store.load_panel_scores("2026-07-07") == {}


def test_run_panel_batch_inflight_dedupe(store, monkeypatch):
    """同一 symbol 正在评分时（inflight），重复批次不会再打。"""
    monkeypatch.setattr(ip, "get_local_store", lambda: store)
    ip._PANEL_INFLIGHT.add("600009")
    try:
        called = []
        monkeypatch.setattr(ip, "investor_panel", lambda s: called.append(s) or {"empty": True})
        ip.run_panel_batch("2026-07-07", ["600009"])
        assert called == []
    finally:
        ip._PANEL_INFLIGHT.discard("600009")
