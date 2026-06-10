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


def test_require_quota_blocks_over_limit(tmp_path, monkeypatch):
    import asyncio
    from fastapi import HTTPException
    import app.lite_billing as lb

    billing = lb.BillingStore(db_path=tmp_path / "q.sqlite")
    monkeypatch.setattr(lb, "billing", billing)
    user = {"id": "u1", "plan": "free", "plan_expires_at": None}

    dep = lb.require_quota("deep_analysis")
    for _ in range(3):  # free 每日 3 次
        asyncio.run(dep.dependency(user=user))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(dep.dependency(user=user))
    assert exc.value.status_code == 402
    assert exc.value.detail["code"] == "quota_exceeded"


def test_require_quota_member_feature_gate(tmp_path, monkeypatch):
    import asyncio
    from fastapi import HTTPException
    import app.lite_billing as lb

    billing = lb.BillingStore(db_path=tmp_path / "q2.sqlite")
    monkeypatch.setattr(lb, "billing", billing)

    dep = lb.require_quota("serenity_deep", feature="serenity_deep")
    free_user = {"id": "u1", "plan": "free", "plan_expires_at": None}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(dep.dependency(user=free_user))
    assert exc.value.detail["code"] == "member_required"

    member = {"id": "u2", "plan": "member", "plan_expires_at": None}
    result = asyncio.run(dep.dependency(user=member))
    assert result["id"] == "u2"


def test_total_used_across_days(billing):
    billing.record("u1", "deep_analysis")
    billing.record("u1", "stock_report", n=4)
    assert billing.total_used("u1") == 5
    assert billing.total_used("u2") == 0
