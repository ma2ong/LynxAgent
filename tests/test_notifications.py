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


def test_wechat_push_member_only(tmp_path, monkeypatch):
    from app.lite_auth import LiteAuthStore
    from app.lite_notifications import NotificationStore
    from quantcore.shared.notify import wechat_push

    db = tmp_path / "notify.sqlite"
    auth = LiteAuthStore(db_path=db)
    auth.create_user("free", "free@n.local", "secret123")
    member = auth.create_user("member", "member@n.local", "secret123")
    with auth.connect() as conn:
        conn.execute("UPDATE users SET plan = ?, plan_expires_at = ? WHERE username = ?", ("member", None, "member"))
        conn.commit()

    calls = []
    monkeypatch.setattr(wechat_push, "_post_json", lambda url, payload, timeout=8: calls.append((url, payload)) or True)
    store = NotificationStore(db_path=db)
    store.bind_wechat("free", serverchan_key="free-key")
    store.bind_wechat("member", serverchan_key="member-key")

    free_result = store.notify_user("free", "t", "c", send_wechat=True)
    member_result = store.notify_user(member["username"], "t", "c", send_wechat=True)

    assert free_result["member_push_allowed"] is False
    assert free_result["wechat_sent"] is False
    assert member_result["member_push_allowed"] is True
    assert member_result["wechat_sent"] is True
    assert len(calls) == 1
    assert "member-key" in calls[0][0]


def test_wechat_bind_requires_token(tmp_path):
    from app.lite_notifications import NotificationStore

    store = NotificationStore(db_path=tmp_path / "notify.sqlite")

    with pytest.raises(ValueError):
        store.bind_wechat("alice", "", "")


def test_effective_plan_expired_member_blocks_push(tmp_path, monkeypatch):
    from app.lite_auth import LiteAuthStore
    from app.lite_notifications import NotificationStore
    from quantcore.shared.notify import wechat_push

    db = tmp_path / "notify.sqlite"
    auth = LiteAuthStore(db_path=db)
    auth.create_user("expired", "expired@n.local", "secret123")
    with auth.connect() as conn:
        conn.execute("UPDATE users SET plan = ?, plan_expires_at = ? WHERE username = ?", ("member", "2020-01-01", "expired"))
        conn.commit()

    calls = []
    monkeypatch.setattr(wechat_push, "_post_json", lambda url, payload, timeout=8: calls.append(url) or True)
    store = NotificationStore(db_path=db)
    store.bind_wechat("expired", serverchan_key="expired-key")

    result = store.notify_user("expired", "t", "c", send_wechat=True)

    assert result["member_push_allowed"] is False
    assert result["wechat_sent"] is False
    assert calls == []
