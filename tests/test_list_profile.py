"""名单入选位置画像：如实标注这套规则在买什么位置的票。

这几个数字是产品对用户的诚实交代，必须**由当日名单实算**。写死成历史快照的话，
名单变了它不变，就从「如实标注」变成了误导。
"""
import pytest

from app.lite_main import _attach_list_profile


def _item(ret20, dist_high20=-3.0):
    return {"entry_position": {"ret20": ret20, "dist_high20": dist_high20}}


def test_profile_reports_median_and_chased_share_of_todays_list():
    data = {"items": [_item(30.0), _item(20.0), _item(10.0), _item(-5.0)]}
    _attach_list_profile(data)
    p = data["list_profile"]
    assert p["median_ret20"] == pytest.approx(15.0)      # (20+10)/2
    assert p["chased_share"] == pytest.approx(0.5)       # 30 和 20 两只 ≥15%
    assert p["samples"] == 4


def test_profile_follows_the_list_instead_of_being_a_fixed_snapshot():
    """换一份名单，画像必须跟着变——这是它与硬编码业绩数字的根本区别。"""
    hot = {"items": [_item(40.0), _item(35.0)]}
    calm = {"items": [_item(2.0), _item(-1.0)]}
    _attach_list_profile(hot)
    _attach_list_profile(calm)
    assert hot["list_profile"]["chased_share"] == 1.0
    assert calm["list_profile"]["chased_share"] == 0.0
    assert hot["list_profile"]["median_ret20"] > calm["list_profile"]["median_ret20"]


def test_profile_is_none_when_no_item_has_a_position():
    """日线不足算不出位置时给 None，不给 0 —— 0% 会被读成「买在原地」。"""
    data = {"items": [{"symbol": "600001"}, {"entry_position": {}}]}
    _attach_list_profile(data)
    assert data["list_profile"] is None
