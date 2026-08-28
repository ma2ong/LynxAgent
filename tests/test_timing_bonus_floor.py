"""时机层加分的分数门槛守卫。

2026-08-28 Allen 定：盘中信号分不到 90 的确认不给加分。此前只看状态不看强度，
一个 60 分的弱确认和一个 95 分的强确认同样拿 +8 —— 加分项失去区分度。

这里钉死两件事：够分的照常加、不够分的一分不加但**标签仍在**（信息不能丢，
用户要看得到「触发过、只是不够强」）。
"""
from __future__ import annotations

import pytest

from app.lite_main import INTRADAY_BONUS_SCORE_FLOOR, _merge_intraday_quality


def _run(status: str, score: float, *, actionable: bool = True, pct: float = 3.0):
    data = {"items": [{"symbol": "600000", "score": 80.0, "pct_chg": pct}]}
    overlay = {
        "is_current": True,
        "signals": {"600000": {"status": status, "score": score, "actionable": actionable}},
    }
    _merge_intraday_quality(data, overlay)
    return data["items"][0]


def test_strong_confirmation_still_gets_full_bonus():
    item = _run("entry", 95.0)
    assert item["timing_adjustment"] == 8.0
    assert item["timing_label"] == "盘中入场确认"


@pytest.mark.parametrize("status, score", [
    ("entry", 89.9),   # 差一点点也不给
    ("entry", 60.0),
    ("watch", 88.0),
])
def test_weak_confirmation_gets_no_bonus(status, score):
    item = _run(status, score)
    assert item["timing_adjustment"] == 0.0
    # 标签必须保留原状态描述，只在后面追加不加分的原因
    assert "不加分" in item["timing_label"]
    assert item["timing_score"] == pytest.approx(score, abs=0.05)


def test_floor_is_the_documented_ninety():
    assert INTRADAY_BONUS_SCORE_FLOOR == 90.0


def test_quality_score_reflects_the_withheld_bonus():
    """够分与不够分之间的差，必须正好是那 8 分 —— 否则门槛没真的作用在排序上。"""
    strong = _run("entry", 95.0)["quality_score"]
    weak = _run("entry", 70.0)["quality_score"]
    assert strong - weak == pytest.approx(8.0, abs=0.05)
