"""入口鉴权闸：/api/* 默认要登录，只放行白名单（2026-09-28 实测 50 个接口公网裸奔）。"""
import uuid

import pytest
from fastapi.testclient import TestClient

import app.lite_main as lite_main
from app.lite_auth import issue_tokens, store


def _token(is_admin=False):
    name = f"u{uuid.uuid4().hex[:8]}"
    store.create_user(name, f"{name}@example.com", "Passw0rd!x", is_admin=is_admin)
    return {"Authorization": f"Bearer {issue_tokens(name)['access_token']}"}


@pytest.fixture
def client():
    return TestClient(lite_main.app)


@pytest.mark.parametrize("method,path", [
    ("post", "/api/lite/smart-pool/tasks"),
    ("post", "/api/lite/pattern-pool/tasks"),
    ("post", "/api/lite/datalake/sync"),
    ("get", "/api/lite/smart-pool"),
    ("get", "/api/lite/heatmap"),
    ("put", "/api/config/settings"),
])
def test_anonymous_requests_are_rejected(client, method, path):
    assert getattr(client, method)(path).status_code == 401


def test_whitelist_stays_public(client):
    assert client.get("/api/health").status_code == 200
    # 登录端点本身必须可达（错口令返回 401 是业务结果，不是闸门）
    r = client.post("/api/auth/login", json={"username": "nobody", "password": "wrong-pass"})
    assert r.json().get("detail") == "用户名或密码错误"


def test_non_admin_cannot_trigger_data_sync(client):
    assert client.post("/api/lite/datalake/sync", headers=_token()).status_code == 403
