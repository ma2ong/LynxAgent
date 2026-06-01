from __future__ import annotations

import importlib.util
import os
from dataclasses import asdict, dataclass
from typing import Any, Literal


OrderSide = Literal["buy", "sell"]


@dataclass(frozen=True)
class EasyTraderOrder:
    code: str
    side: OrderSide
    quantity: int
    price: float
    market: str = "CN"
    order_type: str = "limit"


@dataclass(frozen=True)
class EasyTraderStatus:
    available: bool
    enabled: bool
    mode: str
    client: str | None
    message: str
    live_allowed: bool


class EasyTraderBridge:
    """Safety-first adapter for easytrader.

    The SaaS app should generate orders and risk checks, but should not submit
    real broker orders unless a local operator explicitly enables live mode.
    """

    def __init__(self) -> None:
        self.client = os.getenv("EASYTRADER_CLIENT") or "universal_client"
        self.mode = os.getenv("EASYTRADER_MODE", "paper").lower()
        self.live_allowed = os.getenv("EASYTRADER_ENABLE_LIVE", "").lower() in {"1", "true", "yes"}

    def status(self) -> EasyTraderStatus:
        available = importlib.util.find_spec("easytrader") is not None
        if not available:
            message = "easytrader 未安装，当前仅启用模拟交易。"
        elif not self.live_allowed:
            message = "easytrader 可用，但实盘执行未启用；订单只会进入模拟或待确认流程。"
        else:
            message = "easytrader 可用且已允许本地实盘桥接，仍需本地客户端和人工确认。"
        return EasyTraderStatus(
            available=available,
            enabled=available and self.live_allowed,
            mode=self.mode,
            client=self.client,
            message=message,
            live_allowed=self.live_allowed,
        )

    def validate_order(self, order: EasyTraderOrder) -> list[str]:
        issues: list[str] = []
        if not order.code.strip():
            issues.append("股票代码不能为空")
        if order.side not in {"buy", "sell"}:
            issues.append("交易方向只能是 buy 或 sell")
        if order.quantity <= 0:
            issues.append("数量必须大于 0")
        if order.market == "CN" and order.quantity % 100 != 0:
            issues.append("A 股数量应为 100 股整数倍")
        if order.price <= 0:
            issues.append("价格必须大于 0")
        return issues

    def build_order_intent(self, order: EasyTraderOrder) -> dict[str, Any]:
        issues = self.validate_order(order)
        status = self.status()
        return {
            "adapter": "easytrader",
            "status": asdict(status),
            "order": asdict(order),
            "risk_checks": {
                "passed": not issues,
                "issues": issues,
                "requires_manual_confirm": True,
                "live_execution_blocked": not status.enabled,
            },
            "next_action": "paper_fill" if not status.enabled else "local_confirm_required",
        }

    def submit_live_order(self, order: EasyTraderOrder) -> dict[str, Any]:
        status = self.status()
        issues = self.validate_order(order)
        if issues:
            return {"submitted": False, "message": "风控检查未通过", "issues": issues}
        if not status.enabled:
            return {
                "submitted": False,
                "message": "实盘交易桥未启用。请先在本机配置 EASYTRADER_ENABLE_LIVE=1 并确认券商客户端。",
                "issues": ["live_execution_disabled"],
            }

        import easytrader  # type: ignore

        user = easytrader.use(self.client)
        config_path = os.getenv("EASYTRADER_CONFIG_PATH")
        if config_path:
            user.prepare(config_path)

        if order.side == "buy":
            result = user.buy(order.code, price=order.price, amount=order.quantity)
        else:
            result = user.sell(order.code, price=order.price, amount=order.quantity)
        return {"submitted": True, "message": "订单已提交到本地 easytrader 客户端", "result": result}
