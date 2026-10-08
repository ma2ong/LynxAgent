import pytest


@pytest.fixture()
def stores(tmp_path):
    from app.lite_auth import LiteAuthStore
    from app.lite_billing import BillingStore
    db = tmp_path / "x.sqlite"
    return LiteAuthStore(db_path=db), BillingStore(db_path=db)


def test_list_users_has_usage_and_no_plan(stores):
    """套餐已于 2026-10-08 移除：列表只给用量和启用状态。"""
    auth, billing = stores
    from app.lite_admin import AdminStore
    admin = AdminStore(auth, billing)

    auth.create_user("bob", "bob@x.com", "secret123")
    bob = next(u for u in admin.list_users() if u["username"] == "bob")
    assert bob["used_today"] == 0
    assert "plan" not in bob


def test_set_active(stores):
    auth, billing = stores
    from app.lite_admin import AdminStore
    admin = AdminStore(auth, billing)
    auth.create_user("bob", "bob@x.com", "secret123")
    admin.set_active("bob", False)
    row = auth.get_by_username("bob")
    assert int(row["is_active"]) == 0


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


@pytest.mark.parametrize("value, ok", [
    ("allen@example.com", True),
    ("13800138000", True),      # 中国大陆手机号
    ("19912345678", True),
    ("12345678901", False),     # 1 后面不是 3-9
    ("138001380", False),       # 位数不足
    ("a@b", False),             # 缺顶级域
    ("", False),
])
def test_registration_accepts_email_or_mainland_mobile(value, ok):
    """注册标识改为「邮箱或手机号」二选一，前后端同一口径。"""
    from app.lite_auth import is_valid_contact
    assert is_valid_contact(value) is ok
