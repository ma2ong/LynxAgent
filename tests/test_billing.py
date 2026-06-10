from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture()
def billing(tmp_path):
    from app.lite_billing import BillingStore
    return BillingStore(db_path=tmp_path / "test.sqlite")


def test_used_today_starts_zero(billing):
    assert billing.used_today("u1") == 0


def test_record_and_count(billing):
    billing.record("u1", "deep_analysis")
    billing.record("u1", "serenity_deep", n=2)
    assert billing.used_today("u1") == 3
    assert billing.used_today("u2") == 0  # 不串号


def test_effective_plan_free_default():
    from app.lite_billing import effective_plan
    assert effective_plan({"plan": None}) == "free"
    assert effective_plan({}) == "free"
    assert effective_plan({"plan": "unknown_plan"}) == "free"


def test_effective_plan_member_not_expired():
    from app.lite_billing import effective_plan, beijing_today
    tomorrow = (datetime.now(timezone(timedelta(hours=8))) + timedelta(days=1)).strftime("%Y-%m-%d")
    assert effective_plan({"plan": "member", "plan_expires_at": tomorrow}) == "member"
    # 当天到期 = 仍有效（含当日）
    assert effective_plan({"plan": "member", "plan_expires_at": beijing_today()}) == "member"


def test_effective_plan_member_expired_downgrades():
    from app.lite_billing import effective_plan
    assert effective_plan({"plan": "member", "plan_expires_at": "2020-01-01"}) == "free"
    # 无到期日的 member 视为长期有效（admin 手工开的永久号）
    assert effective_plan({"plan": "member", "plan_expires_at": None}) == "member"


def test_users_table_has_plan_columns(tmp_path):
    from app.lite_auth import LiteAuthStore
    auth = LiteAuthStore(db_path=tmp_path / "auth.sqlite")
    user = auth.create_user("alice", "alice@x.com", "secret123")
    assert user["plan"] == "free"
    assert user["plan_expires_at"] is None


def test_migration_idempotent_on_existing_db(tmp_path):
    from app.lite_auth import LiteAuthStore
    db = tmp_path / "auth.sqlite"
    LiteAuthStore(db_path=db)
    LiteAuthStore(db_path=db)  # 第二次初始化不应报错
