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

    清单历经两次精简：
    - 2026-08-06 删掉「运营微信」（收款改支付宝，该变量已不存在）和「ICP备案」
      （站点跑在 Cloudflare Tunnel / Oracle 上，不解析到境内服务器，备案不适用）。
    - 2026-08-18 删掉两条收款检查（支付宝收款信息 / 自动收款服务商）：产品不再设付费档，
      会员页已无开通入口，配了也没地方展示。
    现在只剩 JWT_SECRET 一条 —— 判据是「没配就真的会出事」。
    """
    from app.lite_main import validate_config

    monkeypatch.delenv("JWT_SECRET", raising=False)

    payload = asyncio.run(validate_config())["data"]

    assert payload["valid"] is False
    failed = {item["key"] for item in payload["checks"] if not item["ok"]}
    assert "jwt_secret" in failed
    # 已废弃的检查项不该再出现，否则页面会继续提示不存在的环境变量
    assert {item["key"] for item in payload["checks"]}.isdisjoint(
        {"icp", "wechat_push", "membership_upgrade", "payment_provider"}
    )


def test_commercial_config_validation_passes_required_env(monkeypatch):
    from app.lite_main import validate_config

    monkeypatch.setenv("JWT_SECRET", "production-secret")

    payload = asyncio.run(validate_config())["data"]

    assert payload["valid"] is True
    required = [item for item in payload["checks"] if item["required"]]
    assert required
    assert all(item["ok"] for item in required)
