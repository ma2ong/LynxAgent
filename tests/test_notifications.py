import pytest


def test_notification_store_unread_and_read(tmp_path):
    from app.lite_auth import LiteAuthStore
    from app.lite_notifications import NotificationStore

    db = tmp_path / "notify.sqlite"
    LiteAuthStore(db_path=db).create_user("alice", "alice@n.local", "secret123")
    store = NotificationStore(db_path=db)

    result = store.notify_user("alice", "title", "content", type_="system")

    assert result["created"] is True
    assert store.unread_count("alice") == 1
    items = store.list("alice")
    assert items[0]["title"] == "title"
    store.mark_read("alice", items[0]["id"])
    assert store.unread_count("alice") == 0


def test_notification_dedupe(tmp_path):
    from app.lite_auth import LiteAuthStore
    from app.lite_notifications import NotificationStore

    db = tmp_path / "notify.sqlite"
    LiteAuthStore(db_path=db).create_user("alice", "alice@n.local", "secret123")
    store = NotificationStore(db_path=db)

    first = store.notify_user("alice", "title", "content", dedupe_key="same")
    second = store.notify_user("alice", "title", "content", dedupe_key="same")

    assert first["created"] is True
    assert second["created"] is False
    assert len(store.list("alice")) == 1


def test_wechat_push_works_for_any_account(tmp_path, monkeypatch):
    """套餐 2026-10-08 移除：普通账号绑了自己的 SendKey 就能收推送（推送走用户自己的通道，不花站点的钱）。"""
    from app.lite_auth import LiteAuthStore
    from app.lite_notifications import NotificationStore
    from quantcore.shared.notify import wechat_push

    db = tmp_path / "notify.sqlite"
    auth = LiteAuthStore(db_path=db)
    auth.create_user("free", "free@n.local", "secret123")

    calls = []
    monkeypatch.setattr(wechat_push, "_post_json", lambda url, payload, timeout=8: calls.append((url, payload)) or True)
    store = NotificationStore(db_path=db)
    store.bind_wechat("free", serverchan_key="free-key")

    result = store.notify_user("free", "t", "c", send_wechat=True)
    assert result["wechat_sent"] is True
    assert len(calls) == 1 and "free-key" in calls[0][0]


def test_wechat_bind_requires_token(tmp_path):
    from app.lite_notifications import NotificationStore

    store = NotificationStore(db_path=tmp_path / "notify.sqlite")

    with pytest.raises(ValueError):
        store.bind_wechat("alice", "", "")
