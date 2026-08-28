"""共振加成的守卫测试。

2026-08-28 修的那个 bug 是这样长出来的：「强度」加分的四行缩进落在了 entry_position
的 `except` 块里，于是只有位置标注**抛异常**时才会执行。上线后一直没人发现，因为它
不报错、不崩溃，只是 tags 里永远没有「强度」、triple_confirm 恒为 false。

这类缺陷没有测试就抓不到——所以这里把四条加分路径逐条钉死，并且专门验证
entry_position 正常算完（不抛异常）时强度加分照样生效。
"""
from __future__ import annotations

import pandas as pd
import pytest

import app.lite_main as lite_main


def _kline(n: int = 60) -> pd.DataFrame:
    close = [10.0 + i * 0.1 for i in range(n)]
    return pd.DataFrame({
        "close": close,
        "high": [c * 1.02 for c in close],
        "low": [c * 0.98 for c in close],
        "volume": [1e6] * n,
        "amount": [1e8] * n,
    })


def _metrics(*, above8=True, above21=True, stack=True):
    return {"dist_from_low": 30.0, "adr": 4.0,
            "above_ema8": above8, "above_ema21": above21, "ema_stack": stack}


def _enrich(monkeypatch, *, patterns=True, sm=_metrics(), kline=None):
    monkeypatch.setattr("quantcore.quant.data.load_local_kline",
                        lambda symbol, days=540: _kline() if kline is None else kline)
    monkeypatch.setattr("quantcore.quant.integrations.recognize_patterns",
                        lambda symbol, data: type("R", (), {"patterns": (
                            [{"active": True, "strength": 85.0, "name": "低位反转"}] if patterns else [])})())
    monkeypatch.setattr("quantcore.quant.relative_strength.compute_strength_metrics",
                        lambda data: sm)
    monkeypatch.setattr("quantcore.quant.risk_check.check_risks",
                        lambda *a, **k: {"risk_count": 0, "advice": "", "flags": []})
    item = {"symbol": "600000", "name": "测试", "smart_score": 80.0}
    lite_main._confluence_enrich_items([item])
    return item


def test_pattern_alone_scores_one_and_a_half(monkeypatch):
    item = _enrich(monkeypatch, patterns=True, sm=_metrics(above8=False, above21=False))
    assert item["confluence_bonus"] == 1.5
    assert item["confluence_tags"] == ["形态"]


def test_strength_scores_even_though_entry_position_computed_cleanly(monkeypatch):
    """这条就是那个 bug 的复现：位置标注正常算完时，强度加分必须照样给。"""
    item = _enrich(monkeypatch, patterns=False, sm=_metrics(stack=False))
    assert "entry_position" in item, "前提：位置标注这轮没抛异常"
    assert "强度" in item["confluence_tags"]
    assert item["confluence_bonus"] == 1.0


def test_ema_stack_adds_the_extra_half(monkeypatch):
    item = _enrich(monkeypatch, patterns=False, sm=_metrics(stack=True))
    assert item["confluence_bonus"] == 1.5  # 1.0 + 0.5


def test_all_three_confluence_reaches_four(monkeypatch):
    """形态 1.5 + 强度 1.0 + 多头排列 0.5 + 三重共振 1.0 = 4.0（也是上限）。"""
    item = _enrich(monkeypatch, patterns=True, sm=_metrics())
    assert item["confluence_bonus"] == 4.0
    assert item["dual_confirm"] is True
    assert item["triple_confirm"] is True


def test_missing_strength_metrics_does_not_crash(monkeypatch):
    """sm 为 None 时不能抛 TypeError —— 旧代码在 except 分支里会二次报错。"""
    item = _enrich(monkeypatch, patterns=True, sm=None)
    assert item["confluence_bonus"] == 1.5
    assert item["triple_confirm"] is False
