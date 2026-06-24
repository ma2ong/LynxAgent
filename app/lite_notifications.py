"""In-app notifications and member WeChat push bindings."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.lite_auth import store as auth_store
from app.lite_billing import effective_plan
from quantcore.shared.notify.wechat_push import WechatPushNotifier


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mask_token(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value)
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}...{text[-4:]}"


class NotificationStore:
    def __init__(self, db_path: Optional[str | Path] = None):
        self.db_path = Path(db_path or auth_store.db_path)
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
                CREATE TABLE IF NOT EXISTS lite_notifications (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    is_read INTEGER NOT NULL DEFAULT 0,
                    dedupe_key TEXT,
                    created_at TEXT NOT NULL,
                    read_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS lite_wechat_bindings (
                    username TEXT PRIMARY KEY,
                    serverchan_key TEXT,
                    pushplus_token TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(lite_notifications)").fetchall()}
            if "dedupe_key" not in columns:
                conn.execute("ALTER TABLE lite_notifications ADD COLUMN dedupe_key TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user_created ON lite_notifications(username, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user_read ON lite_notifications(username, is_read)")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_dedupe ON lite_notifications(dedupe_key)")
            conn.commit()

    def list(self, username: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, type, title, content, payload_json, is_read, created_at, read_at
                FROM lite_notifications
                WHERE username = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (username, max(1, min(int(limit), 100))),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.pop("payload_json") or "{}")
            except json.JSONDecodeError:
                item["payload"] = {}
            item["is_read"] = bool(item["is_read"])
            items.append(item)
        return items

    def unread_count(self, username: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM lite_notifications WHERE username = ? AND is_read = 0",
                (username,),
            ).fetchone()
            return int(row["n"] or 0)

    def mark_read(self, username: str, notification_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE lite_notifications SET is_read = 1, read_at = ? WHERE username = ? AND id = ?",
                (utc_now(), username, notification_id),
            )
            conn.commit()

    def mark_all_read(self, username: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE lite_notifications SET is_read = 1, read_at = ? WHERE username = ? AND is_read = 0",
                (utc_now(), username),
            )
            conn.commit()

    def notify_user(
        self,
        username: str,
        title: str,
        content: str,
        type_: str = "system",
        payload: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
        send_wechat: bool = False,
    ) -> dict[str, Any]:
        payload = payload or {}
        now = utc_now()
        notification_id = hashlib.sha1(f"{username}:{title}:{content}:{now}".encode("utf-8")).hexdigest()[:20]
        with self.connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO lite_notifications (
                        id, username, type, title, content, payload_json, is_read,
                        dedupe_key, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        notification_id,
                        username,
                        type_,
                        title,
                        content,
                        json.dumps(payload, ensure_ascii=False),
                        dedupe_key,
                        now,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                row = conn.execute(
                    "SELECT id, created_at FROM lite_notifications WHERE dedupe_key = ?",
                    (dedupe_key,),
                ).fetchone()
                return {
                    "id": row["id"] if row else None,
                    "created": False,
                    "wechat_sent": False,
                    "member_push_allowed": False,
                }

        wechat_sent = False
        member_push_allowed = False
        if send_wechat:
            user = self._user_by_username(username)
            member_push_allowed = bool(user and effective_plan(dict(user)) == "member")
            if member_push_allowed:
                binding = self.get_wechat_binding(username)
                if binding and binding.get("enabled"):
                    wechat_sent = WechatPushNotifier(
                        serverchan_key=binding.get("serverchan_key"),
                        pushplus_token=binding.get("pushplus_token"),
                    ).send(title, content)

        return {
            "id": notification_id,
            "created": True,
            "wechat_sent": wechat_sent,
            "member_push_allowed": member_push_allowed,
        }

    def _user_by_username(self, username: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    def bind_wechat(
        self,
        username: str,
        serverchan_key: str | None = None,
        pushplus_token: str | None = None,
        enabled: bool = True,
    ) -> None:
        serverchan_key = (serverchan_key or "").strip() or None
        pushplus_token = (pushplus_token or "").strip() or None
        if not serverchan_key and not pushplus_token:
            raise ValueError("至少填写 Server酱 SendKey 或 PushPlus Token")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO lite_wechat_bindings (username, serverchan_key, pushplus_token, enabled, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    serverchan_key = excluded.serverchan_key,
                    pushplus_token = excluded.pushplus_token,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (username, serverchan_key, pushplus_token, 1 if enabled else 0, utc_now()),
            )
            conn.commit()

    def get_wechat_binding(self, username: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT username, serverchan_key, pushplus_token, enabled, updated_at FROM lite_wechat_bindings WHERE username = ?",
                (username,),
            ).fetchone()
        return dict(row) if row else None

    def wechat_status(self, username: str, user: dict[str, Any]) -> dict[str, Any]:
        binding = self.get_wechat_binding(username)
        return {
            "bound": bool(binding and (binding.get("serverchan_key") or binding.get("pushplus_token"))),
            "enabled": bool(binding and binding.get("enabled")),
            "serverchan_key_masked": mask_token(binding.get("serverchan_key") if binding else None),
            "pushplus_token_masked": mask_token(binding.get("pushplus_token") if binding else None),
            "updated_at": binding.get("updated_at") if binding else None,
            "member_push_allowed": effective_plan(user) == "member",
        }

    def unbind_wechat(self, username: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM lite_wechat_bindings WHERE username = ?", (username,))
            conn.commit()

    def notify_favorite_catalysts(self, events: list[dict[str, Any]]) -> int:
        if not events:
            return 0
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT f.username, f.stock_code, f.stock_name, u.plan, u.plan_expires_at
                FROM lite_favorites f
                JOIN users u ON u.username = f.username
                """
            ).fetchall()
        favorites = [dict(row) for row in rows if effective_plan(dict(row)) == "member"]
        if not favorites:
            return 0

        sent = 0
        for event in events:
            title = str(event.get("title") or event.get("event") or event.get("theme") or "催化剂事件").strip()
            beneficiaries = event.get("beneficiaries") or event.get("stocks") or []
            if not isinstance(beneficiaries, list):
                continue
            matched_symbols: set[str] = set()
            matched_names: set[str] = set()
            for item in beneficiaries:
                if not isinstance(item, dict):
                    continue
                symbol = str(item.get("symbol") or item.get("code") or "").strip().zfill(6)
                name = str(item.get("name") or item.get("stock_name") or "").strip()
                if symbol and symbol != "000000":
                    matched_symbols.add(symbol)
                if name:
                    matched_names.add(name)
            if not matched_symbols and not matched_names:
                continue
            event_hash = hashlib.sha1(title.encode("utf-8")).hexdigest()[:12]
            for fav in favorites:
                if fav["stock_code"] not in matched_symbols and fav["stock_name"] not in matched_names:
                    continue
                content = f"{fav['stock_name']} 命中催化剂事件：{title}\n\n仅供研究跟踪，不构成投资建议。"
                result = self.notify_user(
                    fav["username"],
                    f"自选股命中催化剂：{fav['stock_name']}",
                    content,
                    type_="catalyst",
                    payload={"symbol": fav["stock_code"], "event": title},
                    dedupe_key=f"catalyst:{fav['username']}:{fav['stock_code']}:{event_hash}",
                    send_wechat=True,
                )
                if result.get("created"):
                    sent += 1
        return sent


notification_store = NotificationStore()
