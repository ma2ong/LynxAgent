from __future__ import annotations

import hashlib
import json
import re
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import jwt
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field


load_dotenv()

_raw_jwt_secret = os.getenv("JWT_SECRET")
if not _raw_jwt_secret:
    import warnings as _w
    _raw_jwt_secret = secrets.token_hex(32)
    _w.warn(
        "JWT_SECRET not set; using a random session secret. "
        "Tokens will be invalidated on restart. Set JWT_SECRET in .env for stable logins.",
        RuntimeWarning,
        stacklevel=1,
    )
JWT_SECRET: str = _raw_jwt_secret
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_MINUTES = int(os.getenv("SAAS_LITE_ACCESS_TOKEN_MINUTES", "1440"))
REFRESH_TOKEN_DAYS = int(os.getenv("SAAS_LITE_REFRESH_TOKEN_DAYS", "30"))

DEFAULT_PREFERENCES: dict[str, Any] = {
    "language": "zh-CN",
    "ui_theme": "light",
    "default_market": "A股",
    "default_depth": 3,
    "auto_refresh": False,
    "refresh_interval": 300,
    "notifications_enabled": True,
    "email_notifications": False,
    "desktop_notifications": True,
    "sidebar_width": 260,
}


class LoginRequest(BaseModel):
    username: str
    password: str
    remember_me: bool = False


# 邮箱或中国大陆手机号，二选一。手机号仍存进 email 列 —— 那一列只是「唯一登录标识」，
# 为它单开一列会牵动注册/登录/找回/管理员列表四条链路，收益只是名字更贴切。
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


def is_valid_contact(value: str) -> bool:
    v = (value or "").strip()
    return bool(_EMAIL_RE.match(v) or _PHONE_RE.match(v))


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: str  # 邮箱或手机号，见 is_valid_contact
    password: str = Field(min_length=6)
    confirm_password: Optional[str] = None


class RefreshRequest(BaseModel):
    refresh_token: str


class UserUpdateRequest(BaseModel):
    email: Optional[str] = None
    avatar: Optional[str] = None
    preferences: Optional[dict[str, Any]] = None


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6)
    confirm_password: Optional[str] = None


class LiteAuthStore:
    def __init__(self, db_path: Optional[str | Path] = None):
        self.db_path = Path(db_path or os.getenv("SAAS_LITE_DB_PATH", "runtime/lite.sqlite"))
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
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    is_verified INTEGER NOT NULL DEFAULT 1,
                    avatar TEXT,
                    preferences_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login TEXT
                )
                """
            )
            # 开通申请：用户付款后自助提交，管理员一键批准。
            # 支付走人工（支付宝收款码），但「谁付了、付了多少、开通没开通」必须留痕 ——
            # 原先靠加微信口头确认，账记在人脑子里，用户催一次就要翻一次聊天记录。
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS upgrade_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    plan TEXT NOT NULL,
                    order_no TEXT,
                    note TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    handled_at TEXT,
                    handled_by TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_upgrade_requests_status "
                "ON upgrade_requests(status, created_at DESC)"
            )
            # 迁移：老库补 plan 字段（幂等，列已存在则跳过）
            for ddl in (
                "ALTER TABLE users ADD COLUMN plan TEXT NOT NULL DEFAULT 'free'",
                "ALTER TABLE users ADD COLUMN plan_expires_at TEXT",
            ):
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass
            conn.commit()

    def ensure_admin(self) -> dict[str, Any]:
        username = os.getenv("SAAS_LITE_ADMIN_USERNAME") or os.getenv("SAAS_ADMIN_USERNAME") or "admin"
        email = os.getenv("SAAS_LITE_ADMIN_EMAIL") or os.getenv("SAAS_ADMIN_EMAIL") or "admin@local.quantcore"
        password = os.getenv("SAAS_LITE_ADMIN_PASSWORD") or os.getenv("SAAS_ADMIN_PASSWORD") or "admin123"

        user = self.get_by_username(username)
        if user:
            self.sync_admin_password(user, password)
            return self.to_user_dict(user)

        return self.create_user(username=username, email=email, password=password, is_admin=True)

    def create_user(self, username: str, email: str, password: str, is_admin: bool = False) -> dict[str, Any]:
        salt = secrets.token_hex(16)
        now = utc_now()
        user_id = secrets.token_hex(12)
        password_hash = self.hash_password(password, salt)

        try:
            with self.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO users (
                        id, username, email, password_hash, salt, is_admin,
                        preferences_json, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        username,
                        email,
                        password_hash,
                        salt,
                        1 if is_admin else 0,
                        json.dumps(DEFAULT_PREFERENCES, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                conn.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="用户名或邮箱/手机号已存在") from exc

        user = self.get_by_username(username)
        if not user:
            raise HTTPException(status_code=500, detail="创建用户失败")
        return self.to_user_dict(user)

    def sync_admin_password(self, row: sqlite3.Row, password: str) -> None:
        if int(row["is_admin"]) != 1:
            return
        if secrets.compare_digest(self.hash_password(password, row["salt"]), row["password_hash"]):
            return

        salt = secrets.token_hex(16)
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, salt = ?, updated_at = ? WHERE id = ?",
                (self.hash_password(password, salt), salt, now, row["id"]),
            )
            conn.commit()

    def authenticate(self, username: str, password: str) -> Optional[dict[str, Any]]:
        row = self.get_by_username(username)
        if not row and "@" in username:
            row = self.get_by_email(username)
        if not row:
            return None
        if int(row["is_active"]) != 1:
            raise HTTPException(status_code=403, detail="账号已停用")
        if not secrets.compare_digest(self.hash_password(password, row["salt"]), row["password_hash"]):
            return None

        now = utc_now()
        with self.connect() as conn:
            conn.execute("UPDATE users SET last_login = ?, updated_at = ? WHERE id = ?", (now, now, row["id"]))
            conn.commit()

        return self.to_user_dict(self.get_by_username(row["username"]))

    def get_by_username(self, username: str) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    def get_by_email(self, email: str) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

    def update_user(self, username: str, data: UserUpdateRequest) -> dict[str, Any]:
        row = self.get_by_username(username)
        if not row:
            raise HTTPException(status_code=404, detail="用户不存在")

        prefs = self.preferences(row)
        if data.preferences:
            prefs.update(data.preferences)

        email = data.email or row["email"]
        avatar = data.avatar if data.avatar is not None else row["avatar"]
        now = utc_now()
        try:
            with self.connect() as conn:
                conn.execute(
                    """
                    UPDATE users
                    SET email = ?, avatar = ?, preferences_json = ?, updated_at = ?
                    WHERE username = ?
                    """,
                    (email, avatar, json.dumps(prefs, ensure_ascii=False), now, username),
                )
                conn.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="邮箱已存在") from exc

        return self.to_user_dict(self.get_by_username(username))

    def change_password(self, username: str, old_password: str, new_password: str) -> None:
        row = self.get_by_username(username)
        if not row:
            raise HTTPException(status_code=404, detail="用户不存在")
        if not secrets.compare_digest(self.hash_password(old_password, row["salt"]), row["password_hash"]):
            raise HTTPException(status_code=400, detail="旧密码不正确")

        salt = secrets.token_hex(16)
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, salt = ?, updated_at = ? WHERE username = ?",
                (self.hash_password(new_password, salt), salt, now, username),
            )
            conn.commit()

    def to_user_dict(self, row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        return {
            "id": row["id"],
            "username": row["username"],
            "email": row["email"],
            "avatar": row["avatar"],
            "is_active": bool(row["is_active"]),
            "is_verified": bool(row["is_verified"]),
            "is_admin": bool(row["is_admin"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_login": row["last_login"],
            "preferences": self.preferences(row),
            "plan": row["plan"],
            "plan_expires_at": row["plan_expires_at"],
            "daily_quota": 1000,
            "concurrent_limit": 3,
            "total_analyses": 0,
            "successful_analyses": 0,
            "failed_analyses": 0,
        }

    @staticmethod
    def hash_password(password: str, salt: str) -> str:
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000).hex()

    @staticmethod
    def preferences(row: sqlite3.Row) -> dict[str, Any]:
        try:
            prefs = json.loads(row["preferences_json"] or "{}")
        except json.JSONDecodeError:
            prefs = {}
        return {**DEFAULT_PREFERENCES, **prefs}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_token(username: str, token_type: str, expires_delta: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def issue_tokens(username: str) -> dict[str, Any]:
    return {
        "access_token": create_token(username, "access", timedelta(minutes=ACCESS_TOKEN_MINUTES)),
        "refresh_token": create_token(username, "refresh", timedelta(days=REFRESH_TOKEN_DAYS)),
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_MINUTES * 60,
    }


store = LiteAuthStore()
store.ensure_admin()
router = APIRouter(prefix="/api/auth", tags=["lite-auth"])


async def get_current_lite_user(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="未登录")

    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="登录已过期") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="无效登录凭证") from exc

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="无效登录凭证")

    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="无效登录凭证")

    user = store.get_by_username(username)
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    if int(user["is_active"]) != 1:
        raise HTTPException(status_code=403, detail="账号已停用")
    return store.to_user_dict(user)


# 登录限流：公网暴露后，未限流的登录端点等于把口令交给撞库脚本。
# 同一 IP 连续失败 8 次锁 15 分钟；成功即清零。内存态足够——单进程部署，重启即释放。
_LOGIN_FAILS: dict[str, list[float]] = {}
_LOGIN_MAX_FAILS = 8
_LOGIN_WINDOW_SEC = 900


def _login_blocked(client_ip: str) -> bool:
    import time
    fails = [t for t in _LOGIN_FAILS.get(client_ip, []) if time.time() - t < _LOGIN_WINDOW_SEC]
    _LOGIN_FAILS[client_ip] = fails
    return len(fails) >= _LOGIN_MAX_FAILS


def _record_login_fail(client_ip: str) -> None:
    import time
    _LOGIN_FAILS.setdefault(client_ip, []).append(time.time())


def _client_ip(request: Request) -> str:
    # 站点走 Cloudflare Tunnel，request.client.host 永远是隧道本地地址，
    # 真实来源只在 cf-connecting-ip 里。
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "-")


@router.post("/login")
async def login(data: LoginRequest, request: Request):
    client_ip = _client_ip(request)
    if _login_blocked(client_ip):
        raise HTTPException(status_code=429, detail="登录失败次数过多，请 15 分钟后再试")
    user = store.authenticate(data.username, data.password)
    if not user:
        _record_login_fail(client_ip)
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    _LOGIN_FAILS.pop(client_ip, None)
    tokens = issue_tokens(user["username"])
    return {"success": True, "data": {**tokens, "user": user}, "message": "登录成功"}


# 邀请制：公网默认关闭自助注册（ALLOW_REGISTRATION=true 才开放）。
# 账号由管理员在「用户管理」里开——公开注册意味着任何人都能拿到全市场扫描算力。
ALLOW_REGISTRATION = os.getenv("ALLOW_REGISTRATION", "false").lower() in ("1", "true", "yes")

# 注册限流：开放自助注册后没有验证码/短信验证，未限流的注册端点既能被脚本刷号灌垃圾账号，
# 也能被拿来烧 CPU（每次建号跑 12 万轮 pbkdf2）。同一 IP 每小时最多 5 次请求，
# 成败都计数——真人注册一次就够，反复失败本身就是脚本特征。
_REGISTER_HITS: dict[str, list[float]] = {}
_REGISTER_MAX_PER_HOUR = 5
_REGISTER_WINDOW_SEC = 3600


def _register_throttled(client_ip: str) -> bool:
    import time
    now = time.time()
    hits = [t for t in _REGISTER_HITS.get(client_ip, []) if now - t < _REGISTER_WINDOW_SEC]
    hits.append(now)
    _REGISTER_HITS[client_ip] = hits
    return len(hits) > _REGISTER_MAX_PER_HOUR


@router.post("/register")
async def register(data: RegisterRequest, request: Request):
    if not ALLOW_REGISTRATION:
        raise HTTPException(status_code=403, detail="本站为邀请制，暂不开放注册。请联系管理员开通账号。")
    if _register_throttled(_client_ip(request)):
        raise HTTPException(status_code=429, detail="注册过于频繁，请 1 小时后再试")
    if data.confirm_password and data.confirm_password != data.password:
        raise HTTPException(status_code=400, detail="两次输入的密码不一致")
    contact = str(data.email).strip()
    if not is_valid_contact(contact):
        raise HTTPException(status_code=400, detail="请填写有效的邮箱或手机号")
    user = store.create_user(data.username, contact, data.password, is_admin=False)
    return {"success": True, "data": user, "message": "注册成功"}


@router.post("/logout")
async def logout():
    return {"success": True, "data": None, "message": "已退出登录"}


@router.post("/refresh")
async def refresh(data: RefreshRequest):
    try:
        payload = jwt.decode(data.refresh_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="刷新凭证已过期") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="无效刷新凭证") from exc

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="无效刷新凭证")

    username = payload.get("sub")
    if not username or not store.get_by_username(username):
        raise HTTPException(status_code=401, detail="用户不存在")

    return {"success": True, "data": issue_tokens(username), "message": "刷新成功"}


@router.get("/me")
async def me(user: dict[str, Any] = Depends(get_current_lite_user)):
    return {"success": True, "data": user, "message": "ok"}


@router.put("/me")
async def update_me(data: UserUpdateRequest, user: dict[str, Any] = Depends(get_current_lite_user)):
    updated = store.update_user(user["username"], data)
    return {"success": True, "data": updated, "message": "更新成功"}


@router.post("/change-password")
async def change_password(data: ChangePasswordRequest, user: dict[str, Any] = Depends(get_current_lite_user)):
    if data.confirm_password and data.confirm_password != data.new_password:
        raise HTTPException(status_code=400, detail="两次输入的密码不一致")
    store.change_password(user["username"], data.old_password, data.new_password)
    return {"success": True, "data": None, "message": "密码已修改"}


@router.post("/reset-password")
async def reset_password():
    return {"success": True, "data": None, "message": "SaaS Lite 未启用邮件重置，请联系管理员"}


@router.post("/verify-email")
async def verify_email():
    return {"success": True, "data": None, "message": "SaaS Lite 默认邮箱已验证"}
