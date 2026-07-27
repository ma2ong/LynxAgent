import asyncio

import pytest
from fastapi import HTTPException


def test_paper_trading_disabled_by_default():
    from app.routers.paper import PAPER_TRADING_ENABLED, require_paper_trading_enabled

    assert PAPER_TRADING_ENABLED is False
    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_paper_trading_enabled())
    assert exc.value.status_code == 404


def test_commercial_config_validation_marks_missing_required_env(monkeypatch):
    from app.lite_main import validate_config

    for key in ("LYNX_MEMBERSHIP_WECHAT", "LYNX_MEMBERSHIP_QR_URL", "LYNX_ICP_BEIAN", "JWT_SECRET"):
        monkeypatch.delenv(key, raising=False)

    payload = asyncio.run(validate_config())["data"]

    assert payload["valid"] is False
    failed = {item["key"] for item in payload["checks"] if not item["ok"]}
    assert {"membership_upgrade", "icp", "jwt_secret"}.issubset(failed)


def test_commercial_config_validation_passes_required_env(monkeypatch):
    from app.lite_main import validate_config

    monkeypatch.setenv("LYNX_MEMBERSHIP_WECHAT", "ops-wechat")
    monkeypatch.setenv("LYNX_ICP_BEIAN", "粤ICP备00000000号")
    monkeypatch.setenv("JWT_SECRET", "production-secret")

    payload = asyncio.run(validate_config())["data"]

    assert payload["valid"] is True
    required = [item for item in payload["checks"] if item["required"]]
    assert required
    assert all(item["ok"] for item in required)
