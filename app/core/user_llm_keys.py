"""用户自带 LLM 密钥（BYOK）的加密存储。

为什么是 BYOK 而不是站点统一配一把：
产品不收费，推理成本不该由站长垫；而「在设置页填一个全站生效的密钥」意味着任何注册
用户都能改服务端配置，那是个权限洞。每人自带自己的密钥，谁用谁付，互不影响。

加密：Fernet（AES-128-CBC + HMAC），密钥由 JWT_SECRET 派生。这样不引入第二个需要
运维保管的秘密——JWT_SECRET 本来就是生产必配项（配置校验里唯一剩下的那条）。
代价是轮换 JWT_SECRET 会让已存的密钥解不开；这是可接受的：解不开就当没配，
用户重填一次即可，不会造成数据损坏。
"""
from __future__ import annotations

import base64
import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

_DDL = """
CREATE TABLE IF NOT EXISTS user_llm_keys (
    user_id     TEXT PRIMARY KEY,
    provider    TEXT NOT NULL,
    base_url    TEXT NOT NULL,
    model       TEXT NOT NULL,
    key_cipher  TEXT NOT NULL,
    key_tail    TEXT NOT NULL,   -- 末 4 位明文，供界面回显「sk-...abcd」，不足以还原密钥
    updated_at  TEXT NOT NULL
);
"""

# 预置几个 OpenAI 兼容服务商，省得用户自己查 base_url。
PROVIDERS: dict[str, dict[str, str]] = {
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "moonshot": {"label": "Moonshot 月之暗面", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
    "dashscope": {"label": "阿里云百炼", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    "custom": {"label": "自定义（任何 OpenAI 兼容接口）", "base_url": "", "model": ""},
}


def _fernet():
    from cryptography.fernet import Fernet

    secret = os.getenv("JWT_SECRET", "") or "lynx-dev-only-secret"
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


class UserLLMKeyStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(_DDL)

    def _conn(self):
        return sqlite3.connect(self.db_path, timeout=15)

    def save(self, user_id: str, provider: str, base_url: str, model: str, api_key: str) -> None:
        token = _fernet().encrypt(api_key.encode("utf-8")).decode("ascii")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO user_llm_keys(user_id,provider,base_url,model,key_cipher,key_tail,updated_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                "provider=excluded.provider, base_url=excluded.base_url, model=excluded.model, "
                "key_cipher=excluded.key_cipher, key_tail=excluded.key_tail, updated_at=excluded.updated_at",
                (user_id, provider, base_url, model, token, api_key[-4:],
                 datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )

    def delete(self, user_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM user_llm_keys WHERE user_id=?", (user_id,))

    def meta(self, user_id: str) -> Optional[dict]:
        """给界面看的：有没有配、配的哪家、末 4 位。不含密钥本身。"""
        row = self._conn().execute(
            "SELECT provider, base_url, model, key_tail, updated_at FROM user_llm_keys WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        return {"provider": row[0], "base_url": row[1], "model": row[2],
                "key_tail": row[3], "updated_at": row[4]}

    def resolve(self, user_id: str) -> Optional[dict]:
        """给调用方用的：解密后的完整配置。解不开（换过 JWT_SECRET）当作没配。"""
        row = self._conn().execute(
            "SELECT provider, base_url, model, key_cipher FROM user_llm_keys WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        try:
            api_key = _fernet().decrypt(row[3].encode("ascii")).decode("utf-8")
        except Exception:  # noqa: BLE001 — 密钥轮换后旧密文解不开，按未配置处理
            return None
        return {"provider": row[0], "base_url": row[1], "model": row[2], "api_key": api_key}


_store: Optional[UserLLMKeyStore] = None


def get_store() -> UserLLMKeyStore:
    global _store
    if _store is None:
        default = os.path.join("runtime", "lite.sqlite")
        _store = UserLLMKeyStore(os.getenv("SAAS_LITE_DB_PATH", default))
    return _store
