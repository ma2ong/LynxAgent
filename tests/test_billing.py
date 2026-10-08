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


def test_migration_idempotent_on_existing_db(tmp_path):
    from app.lite_auth import LiteAuthStore
    db = tmp_path / "auth.sqlite"
    LiteAuthStore(db_path=db)
    LiteAuthStore(db_path=db)  # 第二次初始化不应报错


def test_require_quota_never_blocks_only_records(tmp_path, monkeypatch):
    """2026-10-08 套餐整体移除：任何账号都不拦，只记账（管理员看「今日用量」）。"""
    import asyncio
    import app.lite_billing as lb

    billing = lb.BillingStore(db_path=tmp_path / "q.sqlite")
    monkeypatch.setattr(lb, "billing", billing)
    user = {"id": "u1", "plan": "free", "plan_expires_at": None}

    dep = lb.require_quota("deep_analysis")
    for _ in range(10):
        asyncio.run(dep.dependency(user=user))  # 不应抛异常
    assert billing.used_today("u1") == 10


def test_total_used_across_days(billing):
    billing.record("u1", "deep_analysis")
    billing.record("u1", "stock_report", n=4)
    assert billing.total_used("u1") == 5
    assert billing.total_used("u2") == 0
