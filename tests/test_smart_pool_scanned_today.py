"""scanned_today 必须落在前端读得到的那一层。

这个字段是「进页面自动跑一次一键智选」的唯一判据。2026-08-28 上线后端到端验证
发现它被写进了响应的**包装层**：`_compute_lite_smart_pool` 返回的已经是
`{"success","data","message"}`，而前端 unwrap() 只取 data —— 字段在外面，前端读到
undefined，`scanned_today !== false` 恒成立，自动扫描永远不触发，而且一声不响。

单测跑不出来（要真扫全市场），所以这里直接钉住字段的**位置**。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.lite_main as lite_main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(lite_main, "_pool_recorded_today", lambda pool: True)
    return TestClient(lite_main.app)


def _unwrap(payload: dict) -> dict:
    """复刻前端 unwrap()：有 data 就取 data。"""
    inner = payload.get("data")
    return inner if isinstance(inner, dict) else payload


def test_field_survives_unwrap_on_the_wrapped_path(client, monkeypatch):
    """正常路径：返回值本身就是包装过的响应。"""
    async def fake(*args, **kwargs):
        return {"success": True, "data": {"items": [{"symbol": "600000"}]}, "message": "ok"}
    monkeypatch.setattr(lite_main, "_compute_lite_smart_pool", fake)

    body = client.get("/api/lite/smart-pool", params={"cache_only": True}).json()
    assert _unwrap(body)["scanned_today"] is True


def test_field_survives_unwrap_on_the_warming_path(client, monkeypatch):
    """冷缓存路径：返回的是没有包装的裸 dict，字段该留在原地。"""
    async def fake(*args, **kwargs):
        return {"items": [], "warming": True, "source": "warming"}
    monkeypatch.setattr(lite_main, "_compute_lite_smart_pool", fake)

    body = client.get("/api/lite/smart-pool", params={"cache_only": True}).json()
    assert _unwrap(body)["scanned_today"] is True


def test_false_reaches_the_frontend_when_the_day_has_no_trail(client, monkeypatch):
    """没扫过必须是 False 而不是缺失 —— 前端判的是 `!== false`。"""
    monkeypatch.setattr(lite_main, "_pool_recorded_today", lambda pool: False)

    async def fake(*args, **kwargs):
        return {"success": True, "data": {"items": []}, "message": "ok"}
    monkeypatch.setattr(lite_main, "_compute_lite_smart_pool", fake)

    body = client.get("/api/lite/smart-pool", params={"cache_only": True}).json()
    assert _unwrap(body)["scanned_today"] is False
