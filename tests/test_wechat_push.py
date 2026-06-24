from quantcore.shared.notify import wechat_push
from quantcore.shared.notify.wechat_push import WechatPushNotifier


def test_wechat_push_skips_without_tokens(monkeypatch):
    monkeypatch.delenv("SERVERCHAN_SENDKEY", raising=False)
    monkeypatch.delenv("SERVERCHAN_KEY", raising=False)
    monkeypatch.delenv("PUSHPLUS_TOKEN", raising=False)

    calls = []
    monkeypatch.setattr(wechat_push, "_post_json", lambda url, payload, timeout=8: calls.append(url) or True)

    assert WechatPushNotifier().send("title", "content") is False
    assert calls == []


def test_wechat_push_uses_serverchan_first(monkeypatch):
    calls = []
    monkeypatch.setattr(wechat_push, "_post_json", lambda url, payload, timeout=8: calls.append((url, payload)) or True)

    ok = WechatPushNotifier(serverchan_key="sct-key", pushplus_token="push-token").send("title", "content")

    assert ok is True
    assert len(calls) == 1
    assert "sct-key.send" in calls[0][0]
    assert calls[0][1]["title"] == "title"


def test_wechat_push_falls_back_to_pushplus(monkeypatch):
    calls = []

    def fake_post(url, payload, timeout=8):
        calls.append(url)
        return "sctapi" not in url

    monkeypatch.setattr(wechat_push, "_post_json", fake_post)

    ok = WechatPushNotifier(serverchan_key="bad-key", pushplus_token="push-token").send("title", "content")

    assert ok is True
    assert len(calls) == 2
    assert calls[1] == "https://www.pushplus.plus/send"

