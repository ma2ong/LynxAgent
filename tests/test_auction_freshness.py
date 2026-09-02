"""竞价名单的「这是哪一天的」判定。

起因（2026-09-02 09:08 实测）：盘前打开页面，行情快照还停在上一交易日，页面照样算出
一份完整名单——概览与名单和前一日完全一致（高开比 32.4%、平均开盘 −0.02%），却没有
任何地方说明它不是今天的。习惯早上看的人会一直把昨天的票当成今日推荐。
"""
from datetime import datetime

from app.routers.insights import _auction_freshness

TODAY = "2026-09-02"          # 周三
YESTERDAY = "2026-09-01"


def _at(hour: int, minute: int, day: int = 2) -> datetime:
    return datetime(2026, 9, day, hour, minute)


def test_pre_auction_names_the_day_the_list_came_from():
    """09:25 之前：快照是昨天的，必须说明这是上一交易日的名单。"""
    f = _auction_freshness(YESTERDAY, "15:00", TODAY, _at(9, 8))
    assert f["state"] == "pre_auction"
    assert f["before_auction"] is True
    assert f["is_today"] is False
    assert YESTERDAY in f["note"]


def test_live_after_the_auction_window():
    """09:25 之后且快照就是今天：正常，不打扰用户。"""
    f = _auction_freshness(TODAY, "09:30", TODAY, _at(9, 40))
    assert f["state"] == "live"
    assert f["note"] == ""
    assert f["is_today"] is True


def test_stale_snapshot_after_the_window_is_a_warning():
    """过了竞价窗口、快照却还停在昨天——这是故障，不是「还没开始」。"""
    f = _auction_freshness(YESTERDAY, "15:00", TODAY, _at(10, 30))
    assert f["state"] == "stale"
    assert f["before_auction"] is False


def test_weekend_morning_is_not_treated_as_pre_auction():
    """周末没有竞价窗口，早上看到的就是上一交易日的名单，属于 stale 而非等待中。"""
    saturday = datetime(2026, 9, 5, 9, 8)
    assert saturday.weekday() >= 5
    f = _auction_freshness(YESTERDAY, "15:00", "2026-09-05", saturday)
    assert f["state"] == "stale"
    assert f["before_auction"] is False


def test_missing_snapshot_date_is_reported_not_guessed():
    """快照不自报日期时如实说不知道，不能拿本机日期顶上。"""
    f = _auction_freshness("", "", TODAY, _at(9, 40))
    assert f["state"] == "unknown"
    assert f["snapshot_date"] is None
    assert f["is_today"] is False
