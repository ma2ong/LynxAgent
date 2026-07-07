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
