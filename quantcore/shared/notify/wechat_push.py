import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)


def _post_json(url: str, payload: dict[str, Any], timeout: int = 8) -> bool:
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        if resp.status_code != 200:
            return False
        data = resp.json()
        code = data.get("code", data.get("errno", 0))
        return code in (0, 200)
    except Exception as exc:
        logger.warning("Wechat push notification failed: %s", exc)
        return False


class WechatPushNotifier:
    """Environment-driven WeChat push adapter. No token is stored in code."""

    def __init__(self, serverchan_key: str | None = None, pushplus_token: str | None = None):
        self.serverchan_key = serverchan_key or os.environ.get("SERVERCHAN_SENDKEY") or os.environ.get("SERVERCHAN_KEY")
        self.pushplus_token = pushplus_token or os.environ.get("PUSHPLUS_TOKEN")

    def send(self, title: str, content: str) -> bool:
        if self.serverchan_key and self._send_serverchan(title, content):
            return True
        if self.pushplus_token and self._send_pushplus(title, content):
            return True
        logger.debug("Wechat push token not configured, skipping notification")
        return False

    def _send_serverchan(self, title: str, content: str) -> bool:
        url = f"https://sctapi.ftqq.com/{self.serverchan_key}.send"
        return _post_json(url, {"title": title, "desp": content})

    def _send_pushplus(self, title: str, content: str) -> bool:
        return _post_json(
            "https://www.pushplus.plus/send",
            {"token": self.pushplus_token, "title": title, "content": content, "template": "markdown"},
        )

