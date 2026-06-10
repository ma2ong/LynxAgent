import pytest


@pytest.fixture()
def stores(tmp_path):
    from app.lite_auth import LiteAuthStore
    from app.lite_billing import BillingStore
    db = tmp_path / "x.sqlite"
    return LiteAuthStore(db_path=db), BillingStore(db_path=db)


def test_set_plan_and_list(stores):
    auth, billing = stores
    from app.lite_admin import AdminStore
    admin = AdminStore(auth, billing)

    auth.create_user("bob", "bob@x.com", "secret123")
    admin.set_plan("bob", "member", "2099-12-31")

    users = admin.list_users()
    bob = next(u for u in users if u["username"] == "bob")
    assert bob["plan"] == "member"
    assert bob["plan_expires_at"] == "2099-12-31"
    assert bob["used_today"] == 0


def test_set_plan_rejects_unknown(stores):
    auth, billing = stores
    from app.lite_admin import AdminStore
    from fastapi import HTTPException
    admin = AdminStore(auth, billing)
    auth.create_user("bob", "bob@x.com", "secret123")
    with pytest.raises(HTTPException):
        admin.set_plan("bob", "platinum", None)


def test_set_active(stores):
    auth, billing = stores
    from app.lite_admin import AdminStore
    admin = AdminStore(auth, billing)
    auth.create_user("bob", "bob@x.com", "secret123")
    admin.set_active("bob", False)
    row = auth.get_by_username("bob")
    assert int(row["is_active"]) == 0


def test_set_plan_rejects_bad_date(stores):
    auth, billing = stores
    from app.lite_admin import AdminStore
    from fastapi import HTTPException
    admin = AdminStore(auth, billing)
    auth.create_user("bob", "bob@x.com", "secret123")
    with pytest.raises(HTTPException):
        admin.set_plan("bob", "member", "31/12/2099")


def test_set_active_protects_admin(stores):
    auth, billing = stores
    from app.lite_admin import AdminStore
    from fastapi import HTTPException
    admin = AdminStore(auth, billing)
    auth.create_user("root", "root@x.com", "secret123", is_admin=True)
    with pytest.raises(HTTPException):
        admin.set_active("root", False)
    # 重新启用不受限
    admin.set_active("root", True)
