# M1 能收钱 — 配额体系 + 双套餐 + 手动开通 + 管理后台 + 合规清洗

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LynxAgent 具备最小商用闭环：LLM 调用受配额控制、免费/会员双套餐生效、admin 可手动开通会员、全站合规文案达标。

**Architecture:** 计费逻辑独立在新文件 `app/lite_billing.py`（PLANS 常量 + BillingStore + require_quota 依赖），管理后台 API 独立在 `app/lite_admin.py`，两者复用 `lite_auth.py` 的 SQLite（`runtime/lite.sqlite`）。前端加一个极简用户状态模块 + 402 拦截 + 三个新页面（会员中心/管理后台/法务页）。

**Tech Stack:** FastAPI + SQLite（后端）、Vue 3 + Element Plus + axios（前端）、pytest（测试）。

**与 spec 的两处实现偏差（已斟酌）：**
1. `plans` 不建表，用代码常量 `PLANS` —— 两档静态套餐，M1 管理后台不编辑套餐定义，建表零收益。
2. serenity prompt 的 `position_note → tracking_note` 改名**不在 M1 做**——M2 会从 TradingAgents 整体同步 serenity 新版（七维评分版），届时一并改，避免改两遍。M1 文案清洗只覆盖前端文案 + AI 输出免责拼接。

**事实基线（写计划时核实）：**
- 后端响应信封：`{"success": bool, "data": ..., "message": str}`；quant router 部分端点返回裸 dict。
- `app/routers/quant.py` 以 `dependencies=[Depends(get_current_lite_user)]` 全局挂载（`lite_main.py:46`），handler 内拿不到 user 对象。
- `users` 表无 plan 字段；`to_user_dict` 的 `daily_quota: 1000` 是假数据。
- 前端 token 存 `localStorage['auth-token']`；`request.ts` 响应拦截器已 unwrap 为 `response.data`；无用户状态 store；Login.vue 无注册表单。
- 仓库无 `tests/` 目录。

---

### Task 1: 计费核心 `app/lite_billing.py`（PLANS + BillingStore + effective_plan）

**Files:**
- Create: `app/lite_billing.py`
- Create: `tests/__init__.py`（空文件）
- Create: `tests/test_billing.py`

- [ ] **Step 1: 确认 pytest 可用**

Run: `python -m pytest --version`
Expected: 输出版本号。若未安装：`pip install pytest`

- [ ] **Step 2: 写失败测试**

创建 `tests/__init__.py`（空文件）和 `tests/test_billing.py`：

```python
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
```

- [ ] **Step 3: 跑测试确认失败**

Run: `python -m pytest tests/test_billing.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.lite_billing'`

- [ ] **Step 4: 实现 `app/lite_billing.py`**

```python
"""配额与套餐：PLANS 常量 + usage_log 记账 + 套餐有效性判定。

套餐为静态两档，定义放代码不建表；usage_log 按北京时间日期分桶。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

CN_TZ = timezone(timedelta(hours=8))

PLANS: dict[str, dict[str, Any]] = {
    "free": {"label": "免费版", "daily_llm": 3, "features": frozenset()},
    "member": {"label": "会员版", "daily_llm": 30, "features": frozenset({"serenity_deep", "lab"})},
}


def beijing_today() -> str:
    return datetime.now(CN_TZ).strftime("%Y-%m-%d")


def effective_plan(user: dict[str, Any]) -> str:
    """返回当前生效套餐 key。会员过期自动视为 free；到期日含当日。"""
    plan = user.get("plan") or "free"
    if plan not in PLANS:
        return "free"
    if plan != "free":
        expires = user.get("plan_expires_at")
        if expires and str(expires)[:10] < beijing_today():
            return "free"
    return plan


class BillingStore:
    def __init__(self, db_path: Optional[str | Path] = None):
        if db_path is None:
            from app.lite_auth import store
            db_path = store.db_path
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    n INTEGER NOT NULL DEFAULT 1,
                    day TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_user_day ON usage_log(user_id, day)")
            conn.commit()

    def used_today(self, user_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(n), 0) AS used FROM usage_log WHERE user_id = ? AND day = ?",
                (user_id, beijing_today()),
            ).fetchone()
            return int(row["used"])

    def record(self, user_id: str, action: str, n: int = 1) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO usage_log (user_id, action, n, day, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, action, n, beijing_today(), datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()

    def total_used(self, user_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(n), 0) AS used FROM usage_log WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            return int(row["used"])
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_billing.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add app/lite_billing.py tests/__init__.py tests/test_billing.py
git commit -m "feat(billing): plans + usage_log store + plan-expiry logic"
```

---

### Task 2: users 表迁移（plan / plan_expires_at）

**Files:**
- Modify: `app/lite_auth.py:89-110`（`init_db`）、`app/lite_auth.py:246-266`（`to_user_dict`）
- Modify: `tests/test_billing.py`（追加测试）

- [ ] **Step 1: 写失败测试（追加到 `tests/test_billing.py`）**

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_billing.py -v -k plan_columns`
Expected: FAIL，`KeyError: 'plan'`（或 sqlite no such column）

- [ ] **Step 3: 修改 `app/lite_auth.py` 的 `init_db`**

在 `init_db` 的 `CREATE TABLE IF NOT EXISTS users (...)` 执行之后、`conn.commit()` 之前，加：

```python
            # 迁移：老库补 plan 字段（幂等，列已存在则跳过）
            for ddl in (
                "ALTER TABLE users ADD COLUMN plan TEXT NOT NULL DEFAULT 'free'",
                "ALTER TABLE users ADD COLUMN plan_expires_at TEXT",
            ):
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass
```

- [ ] **Step 4: 修改 `to_user_dict`**

在 `"preferences": self.preferences(row),` 一行后面加两个真实字段（保留原有 `daily_quota` 等旧键不动，避免破坏前端既有引用）：

```python
            "plan": row["plan"] if "plan" in row.keys() else "free",
            "plan_expires_at": row["plan_expires_at"] if "plan_expires_at" in row.keys() else None,
```

- [ ] **Step 5: 跑全部测试**

Run: `python -m pytest tests/ -v`
Expected: 7 passed

- [ ] **Step 6: 验证现网库迁移安全（runtime/lite.sqlite 是真实数据）**

Run: `python -c "from app.lite_auth import LiteAuthStore; s = LiteAuthStore(); print(s.connect().execute('SELECT username, plan, plan_expires_at FROM users').fetchall())"`
Expected: 输出现有用户列表，plan 全为 `free`，无报错

- [ ] **Step 7: Commit**

```bash
git add app/lite_auth.py tests/test_billing.py
git commit -m "feat(billing): migrate users table with plan/plan_expires_at"
```

---

### Task 3: require_quota 依赖 + GET /api/billing/me

**Files:**
- Modify: `app/lite_billing.py`（追加依赖工厂与 router）
- Modify: `app/lite_main.py:45-46` 附近（挂载 billing router）
- Modify: `tests/test_billing.py`（追加测试）

- [ ] **Step 1: 写失败测试（追加）**

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_billing.py -v -k require_quota`
Expected: FAIL，`AttributeError: ... has no attribute 'billing'`

- [ ] **Step 3: 在 `app/lite_billing.py` 末尾追加**

```python
# ---- FastAPI 集成 ----
from fastapi import APIRouter, Depends, HTTPException  # noqa: E402

from app.lite_auth import get_current_lite_user  # noqa: E402

billing = BillingStore()


def require_quota(action: str, feature: str | None = None, cost: int = 1):
    """依赖工厂：会员特性门禁 + 当日配额扣减。402 detail 带 code 供前端分流。"""

    async def dep(user: dict = Depends(get_current_lite_user)) -> dict:
        plan_key = effective_plan(user)
        plan = PLANS[plan_key]
        if feature and feature not in plan["features"]:
            raise HTTPException(status_code=402, detail={
                "code": "member_required",
                "message": "该功能为会员专属，升级会员后可用",
            })
        if cost > 0:
            used = billing.used_today(user["id"])
            if used + cost > plan["daily_llm"]:
                raise HTTPException(status_code=402, detail={
                    "code": "quota_exceeded",
                    "message": f"今日 AI 分析次数已用完（{plan['daily_llm']} 次/天）",
                    "used": used,
                    "limit": plan["daily_llm"],
                })
            billing.record(user["id"], action, cost)
        return user

    return Depends(dep)


router = APIRouter(prefix="/api/billing", tags=["lite-billing"])


@router.get("/me")
async def billing_me(user: dict = Depends(get_current_lite_user)):
    plan_key = effective_plan(user)
    plan = PLANS[plan_key]
    used = billing.used_today(user["id"])
    return {
        "success": True,
        "data": {
            "plan": plan_key,
            "plan_label": plan["label"],
            "plan_expires_at": user.get("plan_expires_at"),
            "daily_limit": plan["daily_llm"],
            "used_today": used,
            "remaining_today": max(0, plan["daily_llm"] - used),
            "features": sorted(plan["features"]),
        },
        "message": "ok",
    }
```

循环导入说明：`lite_auth` 不导入 `lite_billing`，依赖单向（billing → auth），安全。

注意 `require_quota` 测试里访问的是 `dep.dependency`——因为工厂返回 `Depends(dep)`，测试通过 `.dependency` 拿原函数。

- [ ] **Step 4: 在 `app/lite_main.py` 挂载（line 46 后）**

```python
from app.lite_billing import router as billing_router
app.include_router(billing_router)
```

（import 放文件顶部与 `lite_auth` import 相邻处。）

- [ ] **Step 5: 跑全部测试**

Run: `python -m pytest tests/ -v`
Expected: 9 passed

- [ ] **Step 6: Commit**

```bash
git add app/lite_billing.py app/lite_main.py tests/test_billing.py
git commit -m "feat(billing): require_quota dependency + GET /api/billing/me"
```

---

### Task 4: 给 LLM 端点接配额

**Files:**
- Modify: `app/routers/quant.py`（serenity/deep、report、pipeline/run、pipeline/t5-review、pipeline/quick-critic、research、ml/factor-model、backtest）
- Modify: `app/lite_main.py:3255`（analysis/single）、`app/lite_main.py:3293`（analysis/batch）

动作→配额映射（spec §1：只对烧钱动作计费）：

| 端点 | action | feature 门禁 | 计次 |
|---|---|---|---|
| POST /api/analysis/single | deep_analysis | — | 1 |
| POST /api/analysis/batch | deep_analysis | — | len(symbols) |
| POST /api/quant/serenity/deep | serenity_deep | serenity_deep（会员） | 1 |
| GET /api/quant/report | stock_report | — | 1 |
| POST /api/quant/pipeline/run | pipeline | lab（会员） | 1 |
| POST /api/quant/pipeline/t5-review | pipeline | lab | 1 |
| POST /api/quant/pipeline/quick-critic | pipeline | lab | 1 |
| POST /api/quant/research | — | lab | 0（本地计算不扣次） |
| GET /api/quant/ml/factor-model | — | lab | 0 |
| POST /api/quant/backtest | — | lab | 0 |

- [ ] **Step 1: 修改 `app/routers/quant.py`**

文件顶部 import 区加：

```python
from app.lite_billing import require_quota
```

逐个端点加 user 参数（举两个完整例子，其余同型）：

```python
@router.post("/serenity/deep")
async def serenity_deep(req: SerenityDeepRequest,
                        user: dict = require_quota("serenity_deep", feature="serenity_deep")):
```

```python
@router.get("/report")
async def quant_report(symbol: str, user: dict = require_quota("stock_report")):
```

不计次的纯门禁端点：

```python
@router.post("/backtest")
async def backtest_strategy(req: QuantBacktestRequest,
                            user: dict = require_quota("backtest", feature="lab", cost=0)):
```

按上表把 8 个端点全部加上。handler 体不变，`user` 参数不使用也没关系（依赖已完成校验+记账）。

- [ ] **Step 2: 修改 `app/lite_main.py` 的 analysis/single**

```python
@app.post("/api/analysis/single")
async def single_analysis(req: LiteSingleAnalysisRequest,
                          user: dict[str, Any] = require_quota("deep_analysis")):
```

（顶部 import：`from app.lite_billing import require_quota, billing, effective_plan, PLANS`。原 `Depends(get_current_lite_user)` 被 require_quota 取代——require_quota 内部已含登录校验。）

- [ ] **Step 3: 修改 analysis/batch（变动成本，手动检查）**

batch 当前无登录校验（写计划时核实），顺手补上。在 `@app.post("/api/analysis/batch")` handler 签名加 `user: dict[str, Any] = Depends(get_current_lite_user)`，并在 symbols 解析完成后（去重列表 `symbols` 可用处）插入：

```python
    plan = PLANS[effective_plan(user)]
    used = billing.used_today(user["id"])
    if used + len(symbols) > plan["daily_llm"]:
        return {
            "success": False, "data": None, "code": 402,
            "message": f"批量需 {len(symbols)} 次额度，今日剩余 {max(0, plan['daily_llm'] - used)} 次",
        }
    billing.record(user["id"], "deep_analysis", n=len(symbols))
```

- [ ] **Step 4: 启动冒烟验证**

```bash
python -m uvicorn app.lite_main:app --port 8002 &
sleep 8
# 登录拿 token
TOKEN=$(curl -s -X POST localhost:8002/api/auth/login -H "Content-Type: application/json" -d '{"username":"admin","password":"admin123"}' | python -c "import sys,json;print(json.load(sys.stdin)['data']['access_token'])")
# admin 目前是 free 套餐 → serenity/deep 应 402 member_required
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8002/api/quant/serenity/deep -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"theme":"t"}'
```

Expected: 最后输出 `402`

- [ ] **Step 5: 跑全部测试 + Commit**

Run: `python -m pytest tests/ -v` → 9 passed

```bash
git add app/routers/quant.py app/lite_main.py
git commit -m "feat(billing): wire quota/feature gates into LLM endpoints"
```

---

### Task 5: 管理后台 API `app/lite_admin.py`

**Files:**
- Create: `app/lite_admin.py`
- Modify: `app/lite_main.py`（挂载）
- Create: `tests/test_admin.py`

- [ ] **Step 1: 写失败测试 `tests/test_admin.py`**

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_admin.py -v`
Expected: FAIL，no module `app.lite_admin`

- [ ] **Step 3: 实现 `app/lite_admin.py`**

```python
"""极简管理后台 API：用户列表（含用量）、改套餐、停启用。仅 admin。"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.lite_auth import LiteAuthStore, get_current_lite_user, store as auth_store, utc_now
from app.lite_billing import PLANS, BillingStore, beijing_today, billing as billing_store


class AdminStore:
    def __init__(self, auth: LiteAuthStore, billing: BillingStore):
        self.auth = auth
        self.billing = billing

    def list_users(self) -> list[dict[str, Any]]:
        today = beijing_today()
        with self.auth.connect() as conn:
            rows = conn.execute(
                """
                SELECT u.id, u.username, u.email, u.is_admin, u.is_active,
                       u.plan, u.plan_expires_at, u.created_at, u.last_login,
                       COALESCE(t.used, 0) AS used_today,
                       COALESCE(a.used, 0) AS used_total
                FROM users u
                LEFT JOIN (SELECT user_id, SUM(n) AS used FROM usage_log WHERE day = ? GROUP BY user_id) t
                       ON t.user_id = u.id
                LEFT JOIN (SELECT user_id, SUM(n) AS used FROM usage_log GROUP BY user_id) a
                       ON a.user_id = u.id
                ORDER BY u.created_at DESC
                """,
                (today,),
            ).fetchall()
            return [dict(r) for r in rows]

    def set_plan(self, username: str, plan: str, expires_at: Optional[str]) -> None:
        if plan not in PLANS:
            raise HTTPException(status_code=400, detail=f"未知套餐: {plan}")
        if not self.auth.get_by_username(username):
            raise HTTPException(status_code=404, detail="用户不存在")
        with self.auth.connect() as conn:
            conn.execute(
                "UPDATE users SET plan = ?, plan_expires_at = ?, updated_at = ? WHERE username = ?",
                (plan, expires_at, utc_now(), username),
            )
            conn.commit()

    def set_active(self, username: str, active: bool) -> None:
        if not self.auth.get_by_username(username):
            raise HTTPException(status_code=404, detail="用户不存在")
        with self.auth.connect() as conn:
            conn.execute(
                "UPDATE users SET is_active = ?, updated_at = ? WHERE username = ?",
                (1 if active else 0, utc_now(), username),
            )
            conn.commit()


async def require_admin(user: dict = Depends(get_current_lite_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="仅管理员可用")
    return user


admin_store = AdminStore(auth_store, billing_store)
router = APIRouter(prefix="/api/admin", tags=["lite-admin"], dependencies=[Depends(require_admin)])


class SetPlanRequest(BaseModel):
    plan: str
    plan_expires_at: Optional[str] = None  # YYYY-MM-DD，None=长期


class SetActiveRequest(BaseModel):
    is_active: bool


@router.get("/users")
async def admin_list_users():
    return {"success": True, "data": admin_store.list_users(), "message": "ok"}


@router.put("/users/{username}/plan")
async def admin_set_plan(username: str, req: SetPlanRequest):
    admin_store.set_plan(username, req.plan, req.plan_expires_at)
    return {"success": True, "data": None, "message": f"{username} → {PLANS[req.plan]['label']}"}


@router.put("/users/{username}/active")
async def admin_set_active(username: str, req: SetActiveRequest):
    admin_store.set_active(username, req.is_active)
    return {"success": True, "data": None, "message": "已更新"}
```

注意 `list_users` 的 SQL 跨表（users 在 auth 库、usage_log 在同一个 sqlite 文件）——两个 Store 共用 `runtime/lite.sqlite`，单连接可 JOIN，成立的前提已在 Task 1 保证（BillingStore 默认用 auth_store.db_path）。

- [ ] **Step 4: 挂载（`app/lite_main.py`，billing router 旁）**

```python
from app.lite_admin import router as admin_router
app.include_router(admin_router)
```

- [ ] **Step 5: 跑全部测试**

Run: `python -m pytest tests/ -v`
Expected: 12 passed

- [ ] **Step 6: Commit**

```bash
git add app/lite_admin.py app/lite_main.py tests/test_admin.py
git commit -m "feat(admin): user list with usage + set plan/active endpoints"
```

---

### Task 6: 前端用户状态 + billing API + 402 拦截

**Files:**
- Create: `frontend/src/stores/user.ts`
- Create: `frontend/src/api/billing.ts`
- Modify: `frontend/src/api/request.ts:27-37`（响应拦截器）

- [ ] **Step 1: 创建 `frontend/src/stores/user.ts`**

```typescript
import { ref } from 'vue'
import { ApiClient, type ApiResponse } from '@/api/request'

export interface CurrentUser {
  id: string
  username: string
  email: string
  is_admin: boolean
  plan: string
  plan_expires_at: string | null
}

export const currentUser = ref<CurrentUser | null>(null)

export async function loadCurrentUser(force = false): Promise<CurrentUser | null> {
  if (currentUser.value && !force) return currentUser.value
  try {
    const res = await ApiClient.get<ApiResponse<CurrentUser>>('/api/auth/me')
    currentUser.value = (res?.data as CurrentUser) ?? null
  } catch {
    currentUser.value = null
  }
  return currentUser.value
}

export function clearCurrentUser() {
  currentUser.value = null
}
```

- [ ] **Step 2: 创建 `frontend/src/api/billing.ts`**

```typescript
import { ApiClient, type ApiResponse } from './request'

export interface BillingMe {
  plan: string
  plan_label: string
  plan_expires_at: string | null
  daily_limit: number
  used_today: number
  remaining_today: number
  features: string[]
}

export function fetchBillingMe() {
  return ApiClient.get<ApiResponse<BillingMe>>('/api/billing/me')
}

export interface AdminUser {
  id: string
  username: string
  email: string
  is_admin: number
  is_active: number
  plan: string
  plan_expires_at: string | null
  created_at: string
  last_login: string | null
  used_today: number
  used_total: number
}

export function adminListUsers() {
  return ApiClient.get<ApiResponse<AdminUser[]>>('/api/admin/users')
}

export function adminSetPlan(username: string, plan: string, expiresAt: string | null) {
  return ApiClient.put<ApiResponse<null>>(`/api/admin/users/${username}/plan`, {
    plan,
    plan_expires_at: expiresAt,
  })
}

export function adminSetActive(username: string, isActive: boolean) {
  return ApiClient.put<ApiResponse<null>>(`/api/admin/users/${username}/active`, {
    is_active: isActive,
  })
}
```

- [ ] **Step 3: 修改 `frontend/src/api/request.ts` 响应拦截器**

在 401 处理之后加 402 分支（注意 FastAPI 的 HTTPException dict detail 在 `error.response.data.detail`）：

```typescript
http.interceptors.response.use(
  (response) => response.data,
  (error) => {
    if (error?.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      if (location.pathname !== '/login') location.href = '/login'
    }
    if (error?.response?.status === 402) {
      const detail = error.response.data?.detail
      const msg = detail?.message || '已达套餐限制'
      // 动态 import 避免循环依赖（request.ts 不应静态依赖 element-plus 组件库之外的模块）
      import('element-plus').then(({ ElMessageBox }) => {
        ElMessageBox.confirm(msg, detail?.code === 'member_required' ? '会员专属功能' : '今日额度已用完', {
          confirmButtonText: '了解会员',
          cancelButtonText: '我知道了',
          type: 'warning',
        }).then(() => {
          location.href = '/account/membership'
        }).catch(() => {})
      })
      return Promise.reject(new Error(msg))
    }
    const reason = error?.response?.data?.detail?.message
      || error?.response?.data?.message || error?.message || '请求失败'
    return Promise.reject(new Error(reason))
  },
)
```

- [ ] **Step 4: 类型检查 + Commit**

Run: `cd frontend && npx vue-tsc --noEmit`（若项目无该脚本则 `npm run build` 验证）
Expected: 无新增错误

```bash
git add frontend/src/stores/user.ts frontend/src/api/billing.ts frontend/src/api/request.ts
git commit -m "feat(frontend): user store + billing api + 402 upgrade dialog"
```

---

### Task 7: 侧边栏配额徽章 + 菜单调整（去模拟交易，加会员/管理入口）

**Files:**
- Modify: `frontend/src/components/Layout/AppLayout.vue`
- Modify: `frontend/src/router/index.ts`

- [ ] **Step 1: 修改 `AppLayout.vue` 模板**

a) **删除**模拟交易菜单项（约 line 42-44）：

```html
        <el-menu-item index="/paper">
          <el-icon><Wallet /></el-icon><span>模拟交易</span>
        </el-menu-item>
```

b) 「数据中心」菜单项之后**追加**：

```html
        <el-menu-item index="/account/membership">
          <el-icon><Medal /></el-icon><span>会员与用量</span>
        </el-menu-item>
        <el-menu-item v-if="currentUser?.is_admin" index="/admin/users">
          <el-icon><Setting /></el-icon><span>用户管理</span>
        </el-menu-item>
```

c) `sidebar-foot` 里退出按钮**之前**加配额徽章：

```html
        <div v-if="billingInfo" class="quota-chip" @click="$router.push('/account/membership')">
          {{ billingInfo.plan_label }} · 今日 AI {{ billingInfo.remaining_today }}/{{ billingInfo.daily_limit }}
        </div>
```

- [ ] **Step 2: 修改 `AppLayout.vue` 脚本**

```typescript
import { onMounted, ref } from 'vue'
import { currentUser, loadCurrentUser, clearCurrentUser } from '@/stores/user'
import { fetchBillingMe, type BillingMe } from '@/api/billing'
// icons import 行加: Medal, Setting；删除不再使用的 Wallet

const billingInfo = ref<BillingMe | null>(null)

onMounted(async () => {
  await loadCurrentUser()
  try {
    const res = await fetchBillingMe()
    billingInfo.value = (res?.data as BillingMe) ?? null
  } catch { /* 配额信息拉不到不阻塞页面 */ }
})
```

logout 函数加一行 `clearCurrentUser()`。

样式（`<style>` 内追加）：

```scss
.quota-chip {
  font-size: 12px;
  color: #909399;
  padding: 4px 8px;
  border: 1px solid #e4e7ed;
  border-radius: 10px;
  cursor: pointer;
  margin-bottom: 6px;
  text-align: center;
  &:hover { color: #409eff; border-color: #409eff; }
}
```

- [ ] **Step 3: 修改 `frontend/src/router/index.ts`**

a) 删除 paper 路由行：

```typescript
      { path: 'paper', name: 'paper', component: () => import('@/views/PaperTrading/index.vue') },
```

b) children 里追加（页面在 Task 8/9 创建，先建路由会编译失败——**本步只删 paper，新路由放到 Task 8/9 各自加**）。

- [ ] **Step 4: 验证 + Commit**

Run: `cd frontend && npm run build`
Expected: 构建通过

```bash
git add frontend/src/components/Layout/AppLayout.vue frontend/src/router/index.ts
git commit -m "feat(frontend): quota chip in sidebar; remove paper-trading entry"
```

---

### Task 8: 会员中心页 `Account/Membership.vue`

**Files:**
- Create: `frontend/src/views/Account/Membership.vue`
- Modify: `frontend/src/router/index.ts`（加路由）

- [ ] **Step 1: 创建页面**

```vue
<template>
  <div class="membership">
    <h2>会员与用量</h2>

    <el-card v-if="info" class="card">
      <div class="plan-line">
        <el-tag :type="info.plan === 'member' ? 'warning' : 'info'" effect="dark" size="large">
          {{ info.plan_label }}
        </el-tag>
        <span v-if="info.plan_expires_at" class="expires">有效期至 {{ info.plan_expires_at }}</span>
      </div>
      <div class="usage">
        <span>今日 AI 分析额度</span>
        <el-progress
          :percentage="info.daily_limit ? Math.min(100, (info.used_today / info.daily_limit) * 100) : 0"
          :format="() => `${info!.used_today}/${info!.daily_limit}`"
        />
      </div>
    </el-card>

    <el-card v-if="info?.plan !== 'member'" class="card upgrade">
      <h3>升级会员</h3>
      <ul class="benefits">
        <li>每日 AI 深度分析 3 → 30 次</li>
        <li>解锁催化剂深度报告</li>
        <li>解锁因子实验室与策略回测</li>
      </ul>
      <el-alert type="info" :closable="false"
        title="当前为内测期，开通方式：添加微信并备注注册用户名，人工开通。"
      />
      <div class="contact">
        <!-- TODO(运营)：上线前替换为真实收款码图片 docs/assets/pay-qr.png 与微信号 -->
        <p>联系微信：<b>（上线前填写）</b></p>
      </div>
    </el-card>

    <p class="disclaimer">
      本产品为 AI 研究工具，所有内容仅供研究参考，不构成投资建议。市场有风险，决策需独立。
    </p>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fetchBillingMe, type BillingMe } from '@/api/billing'

const info = ref<BillingMe | null>(null)

onMounted(async () => {
  const res = await fetchBillingMe()
  info.value = (res?.data as BillingMe) ?? null
})
</script>

<style scoped lang="scss">
.membership { max-width: 720px; margin: 0 auto; padding: 16px; }
.card { margin-bottom: 16px; }
.plan-line { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.expires { color: #909399; font-size: 13px; }
.usage { display: flex; flex-direction: column; gap: 8px; font-size: 14px; color: #606266; }
.benefits { margin: 8px 0 16px; padding-left: 20px; color: #606266; line-height: 1.9; }
.contact { margin-top: 12px; font-size: 14px; }
.disclaimer { color: #c0c4cc; font-size: 12px; text-align: center; }
</style>
```

- [ ] **Step 2: 加路由（router children 内）**

```typescript
      { path: 'account/membership', name: 'membership', component: () => import('@/views/Account/Membership.vue') },
```

- [ ] **Step 3: 验证 + Commit**

Run: `cd frontend && npm run build` → 通过

```bash
git add frontend/src/views/Account/Membership.vue frontend/src/router/index.ts
git commit -m "feat(frontend): membership page with usage + manual upgrade info"
```

---

### Task 9: 管理后台页 `Admin/Users.vue`

**Files:**
- Create: `frontend/src/views/Admin/Users.vue`
- Modify: `frontend/src/router/index.ts`（路由 + admin 守卫）

- [ ] **Step 1: 创建页面**

```vue
<template>
  <div class="admin-users">
    <h2>用户管理</h2>
    <el-table :data="users" v-loading="loading" stripe>
      <el-table-column prop="username" label="用户名" width="140" />
      <el-table-column prop="email" label="邮箱" min-width="180" />
      <el-table-column label="套餐" width="160">
        <template #default="{ row }">
          <el-tag :type="row.plan === 'member' ? 'warning' : 'info'" size="small">
            {{ row.plan === 'member' ? '会员' : '免费' }}
          </el-tag>
          <div v-if="row.plan_expires_at" class="sub">至 {{ row.plan_expires_at }}</div>
        </template>
      </el-table-column>
      <el-table-column prop="used_today" label="今日用量" width="90" />
      <el-table-column prop="used_total" label="累计" width="80" />
      <el-table-column prop="last_login" label="最近登录" width="170">
        <template #default="{ row }">{{ (row.last_login || '').slice(0, 16) || '—' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" plain @click="openPlanDialog(row)">改套餐</el-button>
          <el-button size="small" :type="row.is_active ? 'danger' : 'success'" plain
                     :disabled="row.is_admin === 1" @click="toggleActive(row)">
            {{ row.is_active ? '停用' : '启用' }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" title="修改套餐" width="360px">
      <el-form label-width="80px">
        <el-form-item label="套餐">
          <el-radio-group v-model="editPlan">
            <el-radio value="free">免费版</el-radio>
            <el-radio value="member">会员版</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="editPlan === 'member'" label="到期日">
          <el-date-picker v-model="editExpires" type="date" value-format="YYYY-MM-DD"
                          placeholder="留空 = 长期" clearable />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="savePlan">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { adminListUsers, adminSetPlan, adminSetActive, type AdminUser } from '@/api/billing'

const users = ref<AdminUser[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const editPlan = ref('free')
const editExpires = ref<string | null>(null)
let editingUser: AdminUser | null = null

async function load() {
  loading.value = true
  try {
    const res = await adminListUsers()
    users.value = (res?.data as AdminUser[]) ?? []
  } finally {
    loading.value = false
  }
}

function openPlanDialog(row: AdminUser) {
  editingUser = row
  editPlan.value = row.plan
  editExpires.value = row.plan_expires_at
  dialogVisible.value = true
}

async function savePlan() {
  if (!editingUser) return
  await adminSetPlan(editingUser.username, editPlan.value,
    editPlan.value === 'member' ? editExpires.value : null)
  ElMessage.success('已更新')
  dialogVisible.value = false
  await load()
}

async function toggleActive(row: AdminUser) {
  await adminSetActive(row.username, !row.is_active)
  ElMessage.success(row.is_active ? '已停用' : '已启用')
  await load()
}

onMounted(load)
</script>

<style scoped lang="scss">
.admin-users { padding: 16px; }
.sub { font-size: 11px; color: #909399; }
</style>
```

- [ ] **Step 2: 路由 + admin 守卫**

children 追加：

```typescript
      { path: 'admin/users', name: 'admin-users', component: () => import('@/views/Admin/Users.vue'), meta: { requiresAdmin: true } },
```

`router.beforeEach` 改为（保留原逻辑，追加 admin 检查）：

```typescript
router.beforeEach(async (to) => {
  const token = localStorage.getItem('auth-token')
  if (to.meta.requiresAuth && !token) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'login' && token) {
    return { name: 'quant' }
  }
  if (to.meta.requiresAdmin) {
    const { loadCurrentUser } = await import('@/stores/user')
    const user = await loadCurrentUser()
    if (!user?.is_admin) return { path: '/' }
  }
  return true
})
```

（后端已有 `require_admin` 硬校验，前端守卫只是体验层。）

- [ ] **Step 3: 验证 + Commit**

Run: `cd frontend && npm run build` → 通过

```bash
git add frontend/src/views/Admin/Users.vue frontend/src/router/index.ts
git commit -m "feat(frontend): admin users page with plan/active management"
```

---

### Task 10: 注册表单 + 免责勾选 + 法务静态页

**Files:**
- Modify: `frontend/src/views/Auth/Login.vue`（加注册 tab）
- Create: `frontend/src/views/Legal/Terms.vue`
- Create: `frontend/src/views/Legal/Privacy.vue`
- Modify: `frontend/src/router/index.ts`

- [ ] **Step 1: 创建 `frontend/src/views/Legal/Terms.vue`**

```vue
<template>
  <div class="legal">
    <h1>用户协议</h1>
    <p class="updated">更新日期：2026-06-10</p>
    <h3>1. 服务性质</h3>
    <p>LynxAgent（下称"本产品"）是一款 AI 量化研究工具，提供市场数据整理、量化信号计算与 AI 辅助研究分析。本产品不具备证券投资咨询资质，所有输出内容（包括但不限于评分、信号、AI 生成的分析报告）均为算法对公开数据的自动处理结果，仅供学习与研究参考，不构成任何投资建议、要约或承诺。</p>
    <h3>2. 用户责任</h3>
    <p>用户基于本产品内容作出的任何投资决策及其后果，由用户自行承担。证券市场有风险，可能导致本金损失。</p>
    <h3>3. 账号与套餐</h3>
    <p>账号仅限本人使用，禁止共享、转售。付费套餐按开通时约定的期限与额度提供服务；因违反本协议被停用的账号，已付费用不予退还。</p>
    <h3>4. 数据来源</h3>
    <p>本产品数据来自公开渠道，不保证实时性、准确性与完整性。</p>
    <h3>5. 服务变更</h3>
    <p>本产品有权调整功能与套餐内容，重大变更将提前在产品内公告。</p>
    <router-link to="/login">返回登录</router-link>
  </div>
</template>

<style scoped>
.legal { max-width: 720px; margin: 40px auto; padding: 0 24px; line-height: 1.8; color: #303133; }
.updated { color: #909399; font-size: 13px; }
</style>
```

- [ ] **Step 2: 创建 `frontend/src/views/Legal/Privacy.vue`**

```vue
<template>
  <div class="legal">
    <h1>隐私政策</h1>
    <p class="updated">更新日期：2026-06-10</p>
    <h3>1. 收集的信息</h3>
    <p>注册时收集用户名与邮箱；使用过程中记录功能调用日志（用于配额计算与服务改进）。不收集身份证件、银行账户、证券账户等敏感信息。</p>
    <h3>2. 信息的使用</h3>
    <p>仅用于提供服务、配额管理与必要的服务通知，不向任何第三方出售或共享个人信息。</p>
    <h3>3. 数据存储与安全</h3>
    <p>密码以加盐哈希存储，明文不可还原。数据存储于服务运营方控制的服务器。</p>
    <h3>4. 注销</h3>
    <p>用户可联系管理员注销账号，注销后个人信息将被删除。</p>
    <router-link to="/login">返回登录</router-link>
  </div>
</template>

<style scoped>
.legal { max-width: 720px; margin: 40px auto; padding: 0 24px; line-height: 1.8; color: #303133; }
.updated { color: #909399; font-size: 13px; }
</style>
```

- [ ] **Step 3: 加路由（顶层，不在 AppLayout 内，无需登录）**

```typescript
  { path: '/legal/terms', name: 'terms', component: () => import('@/views/Legal/Terms.vue') },
  { path: '/legal/privacy', name: 'privacy', component: () => import('@/views/Legal/Privacy.vue') },
```

- [ ] **Step 4: Login.vue 加注册表单**

先读现有 `frontend/src/views/Auth/Login.vue` 全文，保持其样式与提交模式。改动要点：

a) 顶部加 tab 切换（登录 / 注册），用 `const mode = ref<'login' | 'register'>('login')`。

b) 注册表单字段：用户名、邮箱、密码、确认密码 + **强制勾选**：

```html
<el-checkbox v-model="agreedDisclaimer">
  我已阅读并同意
  <router-link to="/legal/terms" target="_blank">《用户协议》</router-link>、
  <router-link to="/legal/privacy" target="_blank">《隐私政策》</router-link>，
  并理解本产品仅供研究参考，不构成投资建议
</el-checkbox>
```

c) 提交逻辑：

```typescript
import { ApiClient } from '@/api/request'
import { ElMessage } from 'element-plus'

const agreedDisclaimer = ref(false)
const regForm = ref({ username: '', email: '', password: '', confirm_password: '' })

async function doRegister() {
  if (!agreedDisclaimer.value) {
    ElMessage.warning('请先阅读并勾选用户协议与免责声明')
    return
  }
  if (regForm.value.password !== regForm.value.confirm_password) {
    ElMessage.warning('两次输入的密码不一致')
    return
  }
  await ApiClient.post('/api/auth/register', regForm.value)
  ElMessage.success('注册成功，请登录')
  mode.value = 'login'
}
```

- [ ] **Step 5: 验证 + Commit**

Run: `cd frontend && npm run build` → 通过
手动验证：`npm run dev` 打开 /login → 切注册 tab → 不勾选提交被拦 → 勾选注册成功 → 新账号登录后侧边栏显示「免费版 · 今日 AI 3/3」。

```bash
git add frontend/src/views/Legal/ frontend/src/views/Auth/Login.vue frontend/src/router/index.ts
git commit -m "feat(frontend): register form with disclaimer consent + legal pages"
```

---

### Task 11: 文案清洗 + AI 输出免责拼接

**Files:**
- Modify: `frontend/src/components/Layout/AppLayout.vue`（利好监控 → 催化剂监控）
- Modify: `frontend/src/views/Insights/CatalystMonitor.vue`（页面标题同步）
- Create: `quantcore/shared/disclaimer.py`
- Modify: `quantcore/quant/serenity_service.py`（deep_for_theme 拼免责）
- Modify: `app/lite_main.py`（深度分析任务结果拼免责）

- [ ] **Step 1: 菜单与页面改名**

`AppLayout.vue`：`<span>利好监控</span>` → `<span>催化剂监控</span>`
`CatalystMonitor.vue`：页面 `<h2>` 等出现「利好监控」处全部改「催化剂监控」（先 `grep -n "利好" frontend/src/views/Insights/CatalystMonitor.vue` 列出再逐个改）。

- [ ] **Step 2: 创建 `quantcore/shared/disclaimer.py`**

```python
"""统一免责声明：所有 AI 生成内容由代码层拼接，不依赖模型自觉。"""

AI_DISCLAIMER = (
    "本内容由 AI 基于公开数据自动生成，仅供研究参考，"
    "不构成投资建议。市场有风险，决策需独立。"
)


def attach_disclaimer(payload: dict) -> dict:
    """给 AI 输出 dict 附加免责字段（幂等）。"""
    if isinstance(payload, dict):
        payload.setdefault("disclaimer", AI_DISCLAIMER)
    return payload
```

- [ ] **Step 3: 接入 serenity 深度报告**

`quantcore/quant/serenity_service.py` 的 `deep_for_theme`：

```python
def deep_for_theme(theme: str, event: str, beneficiaries: List[dict]) -> dict:
    from quantcore.shared.disclaimer import attach_disclaimer
    rep = deep_report(theme, event, beneficiaries)
    return attach_disclaimer(rep) if rep else {"error": "深度分析失败，请重试"}
```

- [ ] **Step 4: 接入深度多智能体分析结果**

`app/lite_main.py` 中 `_run_lite_single_analysis_task` 完成处（grep `result_data` 赋值点），对最终 `result_data` dict 调 `attach_disclaimer`。先 `grep -n "result_data" app/lite_main.py | head` 找到任务成功写回处，在写回前加：

```python
from quantcore.shared.disclaimer import attach_disclaimer
result_data = attach_disclaimer(result_data)
```

（import 放文件顶部。`report_service.build_stock_report` 已自带 disclaimer 字段（line 442），不动。）

- [ ] **Step 5: 全站扫描遗漏**

Run: `grep -rn "建议买入\|建议卖出\|建议加仓\|建议减仓\|目标价" frontend/src quantcore app --include="*.vue" --include="*.py" --include="*.ts" | grep -v node_modules`
Expected: 无输出（有则逐条评估清洗）

- [ ] **Step 6: 测试 + Commit**

Run: `python -m pytest tests/ -v` → 全过；`cd frontend && npm run build` → 通过

```bash
git add frontend/src/components/Layout/AppLayout.vue frontend/src/views/Insights/CatalystMonitor.vue quantcore/shared/disclaimer.py quantcore/quant/serenity_service.py app/lite_main.py
git commit -m "chore(compliance): rename catalyst page, attach AI disclaimer at code layer"
```

---

### Task 12: M1 验收冒烟（对照 spec 验收标准）

**Files:** 无新文件，验证性任务。

- [ ] **Step 1: 启动前后端**

```bash
python -m uvicorn app.lite_main:app --port 8001 &
cd frontend && npm run dev &
```

- [ ] **Step 2: 验收项 1 — 免费账号配额拦截**

注册一个新账号（带免责勾选）→ 登录 → 对任一股票连续发起 4 次深度分析。
Expected: 第 4 次弹「今日额度已用完」对话框，点「了解会员」跳转会员中心。

- [ ] **Step 3: 验收项 2 — admin 改套餐立即生效**

admin 登录 → 用户管理 → 把该账号改为会员（到期日设明天）→ 该账号刷新页面。
Expected: 侧边栏显示「会员版 · 今日 AI 26/30」（已用 3 次 + 拦截那次未扣），催化剂深度报告可用。

- [ ] **Step 4: 验收项 3 — 到期自动降级**

admin 把该账号到期日改为昨天 → 该账号再发起催化剂深度报告。
Expected: 402「会员专属功能」。

- [ ] **Step 5: 验收项 4 — 合规文案**

Run: `grep -rn "建议仓位\|建议加仓\|止损\|利好监控" frontend/src --include="*.vue" | grep -v node_modules`
Expected: 无输出。
检查任一 AI 深度报告响应 JSON 含 `disclaimer` 字段。

- [ ] **Step 6: 验收项 5 — 模拟交易不可达**

访问 `/paper`。Expected: 路由不存在（跳转或 404），侧边栏无入口。

- [ ] **Step 7: 完成提交**

```bash
git add -A
git commit -m "chore: M1 monetization acceptance verified"
```

---

## 自审记录

- **Spec 覆盖**：配额体系（T1-4）、双套餐（T1）、手动开通（T5+T8 收款信息位）、管理后台（T5+T9）、文案合规（T10-11）、PaperTrading 下线（T7）、验收标准（T12）。ICP 备案为运营动作不在代码计划内（spec 已注明尽早启动）。
- **类型一致性**：`require_quota` 返回 `Depends(...)`，handler 签名统一 `user: dict = require_quota(...)`；前端 `ApiResponse<T>` 信封统一。
- **遗留风险**：Login.vue 改动依赖现场结构（计划已指示先读全文再改）；`_run_lite_single_analysis_task` 的 result_data 写回点需 grep 定位（已给命令）。
