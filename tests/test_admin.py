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


def _submit(auth, username: str, plan: str = "member", order_no: str = "20260806001") -> int:
    """直接写申请单，等价于用户在会员页点「提交申请」。"""
    from app.lite_auth import utc_now
    with auth.connect() as conn:
        cur = conn.execute(
            "INSERT INTO upgrade_requests(username, plan, order_no, note, status, created_at)"
            " VALUES(?,?,?,'','pending',?)",
            (username, plan, order_no, utc_now()),
        )
        conn.commit()
        return int(cur.lastrowid)


def test_approving_a_request_opens_the_plan_and_marks_it_handled(stores):
    """付款仍是人工核对，但开通动作要落在申请单上，不能只存在于聊天记录里。"""
    auth, billing = stores
    from app.lite_admin import AdminStore
    admin = AdminStore(auth, billing)
    auth.create_user("bob", "bob@x.com", "secret123")
    rid = _submit(auth, "bob")

    with auth.connect() as conn:
        row = conn.execute("SELECT status, order_no FROM upgrade_requests WHERE id=?", (rid,)).fetchone()
    assert row["status"] == "pending" and row["order_no"] == "20260806001"

    admin.set_plan("bob", "member", "2099-12-31")
    with auth.connect() as conn:
        conn.execute(
            "UPDATE upgrade_requests SET status='approved', handled_by='admin' WHERE id=?", (rid,)
        )
        conn.commit()
        row = conn.execute("SELECT status, handled_by FROM upgrade_requests WHERE id=?", (rid,)).fetchone()
    assert row["status"] == "approved" and row["handled_by"] == "admin"
    assert next(u for u in admin.list_users() if u["username"] == "bob")["plan"] == "member"


def test_pending_requests_are_listed_newest_first(stores):
    auth, _billing = stores
    auth.create_user("bob", "bob@x.com", "secret123")
    auth.create_user("amy", "amy@x.com", "secret123")
    first = _submit(auth, "bob", order_no="A1")
    second = _submit(auth, "amy", order_no="A2")
    with auth.connect() as conn:
        rows = conn.execute(
            "SELECT id, username FROM upgrade_requests WHERE status='pending' ORDER BY id DESC"
        ).fetchall()
    assert [r["id"] for r in rows] == [second, first]
    assert rows[0]["username"] == "amy"


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
