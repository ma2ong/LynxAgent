"""每日盘报（daily_reports 表 + report_daily 生成器）回归测试。"""
import pytest

from quantcore.quant.local_store import LocalQuantStore


@pytest.fixture()
def store(tmp_path):
    return LocalQuantStore(str(tmp_path / "test.sqlite"))


def test_daily_report_roundtrip(store):
    content = {"kind": "close", "date": "2026-07-06", "llm": False,
               "sections": [{"title": "一句话定调", "body": "市场偏冷"}]}
    store.save_daily_report("2026-07-06", "close", content)
    loaded = store.load_daily_report("2026-07-06", "close")
    assert loaded == content
    # 同日同 kind 重存覆盖，不重复
    store.save_daily_report("2026-07-06", "close", {**content, "llm": True})
    assert store.load_daily_report("2026-07-06", "close")["llm"] is True


def test_daily_report_latest_and_list(store):
    store.save_daily_report("2026-07-03", "close", {"d": 1})
    store.save_daily_report("2026-07-06", "close", {"d": 2})
    store.save_daily_report("2026-07-06", "premarket", {"d": 3})
    assert store.latest_daily_report("close") == {"d": 2}
    assert store.latest_daily_report("premarket") == {"d": 3}
    assert store.latest_daily_report("nope") is None
    dates = store.list_report_dates(30)
    assert {"date": "2026-07-06", "kind": "premarket"} in dates
    assert len(dates) == 3


def test_load_daily_report_missing_returns_none(store):
    assert store.load_daily_report("2026-01-01", "close") is None


from quantcore.quant import report_daily


def _stub_facts(monkeypatch, store):
    """隔离外部数据：facts 收集全部打桩，测试只验证组装/降级/落库逻辑。"""
    monkeypatch.setattr(report_daily, "_gather_close_facts", lambda: {
        "date": "2026-07-06",
        "market_context": {"state": "偏冷", "advice": "建议降低仓位", "as_of": "2026-07-06"},
        "limit_up": {"total": 35},
        "sentiment": {"median_chg": -0.8},
        "picks_stats": {"pools": []},
    })
    monkeypatch.setattr(report_daily, "get_local_store", lambda: store)


def test_generate_close_report_without_llm(monkeypatch, store):
    _stub_facts(monkeypatch, store)
    monkeypatch.setattr(report_daily.llm, "chat_json", lambda *a, **k: None)
    content = report_daily.generate_report("close")
    assert content["kind"] == "close"
    assert content["llm"] is False
    titles = [s["title"] for s in content["sections"]]
    assert "一句话定调" in titles
    # 已落库
    assert store.latest_daily_report("close")["kind"] == "close"


def test_generate_close_report_with_llm(monkeypatch, store):
    _stub_facts(monkeypatch, store)
    fake = {"sections": [
        {"title": "一句话定调", "body": "缩量普跌，防守为主。"},
        {"title": "主线分析", "body": "无明显主线。"},
        {"title": "热门追踪", "body": "涨停 35 家。"},
        {"title": "明日看点", "body": "关注量能。"},
        {"title": "核心结论", "body": "轻仓观望。"},
    ]}
    monkeypatch.setattr(report_daily.llm, "chat_json", lambda *a, **k: fake)
    content = report_daily.generate_report("close")
    assert content["llm"] is True
    assert content["sections"][0]["body"] == "缩量普跌，防守为主。"


def test_generate_premarket_uses_extra(monkeypatch, store):
    monkeypatch.setattr(report_daily, "get_local_store", lambda: store)
    monkeypatch.setattr(report_daily, "_gather_premarket_facts", lambda extra: {
        "date": "2026-07-06", "market_context": {"state": "中性"},
        "auction": (extra or {}).get("auction") or {},
        "catalysts": (extra or {}).get("catalysts") or {},
    })
    monkeypatch.setattr(report_daily.llm, "chat_json", lambda *a, **k: None)
    content = report_daily.generate_report("premarket", {"auction": {"summary": "高开"}})
    assert content["kind"] == "premarket"
    assert content["facts"]["auction"] == {"summary": "高开"}


def test_generate_report_rejects_bad_kind(store):
    with pytest.raises(ValueError):
        report_daily.generate_report("weekly")


def test_llm_result_missing_sections_falls_back(monkeypatch, store):
    _stub_facts(monkeypatch, store)
    monkeypatch.setattr(report_daily.llm, "chat_json", lambda *a, **k: {"foo": 1})
    content = report_daily.generate_report("close")
    assert content["llm"] is False  # 非法 LLM 输出走降级
