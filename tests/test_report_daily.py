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
