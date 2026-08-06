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
    """必填项缺失时必须报不合格。

    2026-08-06 清单精简：删掉「运营微信」（收款改支付宝，该变量已不存在）和「ICP备案」
    （站点跑在 Cloudflare Tunnel / Oracle 上，不解析到境内服务器，备案不适用；真正的
    约束是支付服务商的签约条款，属于选型问题不是部署前置项）。
    """
    from app.lite_main import validate_config

    for key in ("LYNX_MEMBERSHIP_ALIPAY", "LYNX_MEMBERSHIP_QR_URL", "JWT_SECRET"):
        monkeypatch.delenv(key, raising=False)

    payload = asyncio.run(validate_config())["data"]

    assert payload["valid"] is False
    failed = {item["key"] for item in payload["checks"] if not item["ok"]}
    assert {"membership_upgrade", "jwt_secret"}.issubset(failed)
    # 已废弃的检查项不该再出现，否则页面会继续提示不存在的环境变量
    assert {item["key"] for item in payload["checks"]}.isdisjoint({"icp", "wechat_push"})


def test_commercial_config_validation_passes_required_env(monkeypatch):
    from app.lite_main import validate_config

    monkeypatch.setenv("LYNX_MEMBERSHIP_ALIPAY", "allen@example.com")
    monkeypatch.setenv("JWT_SECRET", "production-secret")

    payload = asyncio.run(validate_config())["data"]

    assert payload["valid"] is True
    required = [item for item in payload["checks"] if item["required"]]
    assert required
    assert all(item["ok"] for item in required)
