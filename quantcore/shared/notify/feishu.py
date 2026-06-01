import os
import logging
import requests

logger = logging.getLogger(__name__)


class FeishuNotifier:
    def __init__(self, webhook_url: str | None = None):
        self.webhook_url = webhook_url or os.environ.get("FEISHU_WEBHOOK_URL")

    def send_text(self, text: str) -> bool:
        if not self.webhook_url:
            logger.debug("Feishu webhook not configured, skipping notification")
            return False
        payload = {"msg_type": "text", "content": {"text": text}}
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=5)
            if resp.status_code != 200:
                return False
            return resp.json().get("code", 0) == 0
        except Exception as e:
            logger.warning(f"Feishu notification failed: {e}")
            return False

    def send_markdown(self, title: str, content: str) -> bool:
        if not self.webhook_url:
            logger.debug("Feishu webhook not configured, skipping notification")
            return False
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": title}},
                "elements": [{"tag": "markdown", "content": content}],
            },
        }
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=5)
            if resp.status_code != 200:
                return False
            return resp.json().get("code", 0) == 0
        except Exception as e:
            logger.warning(f"Feishu markdown notification failed: {e}")
            return False
