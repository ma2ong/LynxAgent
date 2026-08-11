"""注册端点的按 IP 限流。

不挂整个 lite_main（会连带起后台调度和板块保温），只挂 auth router——
要验的是"限流真的接在了 /register 上"这条线，不是应用启动。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path):
    from app import lite_auth

    monkeypatch.setattr(lite_auth, "ALLOW_REGISTRATION", True)
    # 每个用例一个空库：共享的临时库会让上一轮建的账号在下一轮撞 409。
    monkeypatch.setattr(lite_auth, "store", lite_auth.LiteAuthStore(db_path=tmp_path / "lite.sqlite"))
    lite_auth._REGISTER_HITS.clear()

    api = FastAPI()
    api.include_router(lite_auth.router)
    return TestClient(api)


def _register(client, username, ip):
    return client.post(
        "/api/auth/register",
        json={"username": username, "email": f"{username}@x.com", "password": "secret123"},
        headers={"cf-connecting-ip": ip},
    )


def test_sixth_attempt_in_an_hour_is_throttled(client):
    for i in range(5):
        assert _register(client, f"bot{i}", "9.9.9.9").status_code == 200

    blocked = _register(client, "bot5", "9.9.9.9")
    assert blocked.status_code == 429
    assert "注册过于频繁" in blocked.json()["detail"]


def test_throttle_counts_failed_attempts_too(client):
    # 密码不一致这类失败也计数：脚本探测不该因为请求写得不对就免费。
    for i in range(5):
        resp = client.post(
            "/api/auth/register",
            json={
                "username": f"probe{i}",
                "email": f"probe{i}@x.com",
                "password": "secret123",
                "confirm_password": "mismatch",
            },
            headers={"cf-connecting-ip": "8.8.8.8"},
        )
        assert resp.status_code == 400

    assert _register(client, "probe5", "8.8.8.8").status_code == 429


def test_throttle_is_per_ip(client):
    for i in range(5):
        assert _register(client, f"alice{i}", "1.1.1.1").status_code == 200
    assert _register(client, "alice5", "1.1.1.1").status_code == 429

    # 另一个 IP 不受影响
    assert _register(client, "bella0", "2.2.2.2").status_code == 200


def test_registration_gate_runs_before_throttle(client, monkeypatch):
    from app import lite_auth

    monkeypatch.setattr(lite_auth, "ALLOW_REGISTRATION", False)
    for _ in range(8):
        resp = _register(client, "nobody", "3.3.3.3")
        assert resp.status_code == 403
