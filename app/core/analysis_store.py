"""分析历史与研报全文索引的读写（从 lite_main 抽出）。

写侧（保存历史、写 FTS 索引）在分析任务里调用，读侧（FTS 检索）在研报搜索路由里
调用——两端分属不同 router，落在这里两边都能直接 import。

写失败一律吞掉：留痕和检索索引是辅助功能，不能让它拖垮分析主流程。
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from app.core.schema import ensure_lite_analysis_history_table, ensure_lite_report_fts_table
from app.lite_auth import store


def _save_analysis_history(
    username: str,
    symbol: str,
    stock_name: str | None,
    market: str,
    overall_rating: str | None,
    score: float | None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    record_id = secrets.token_hex(8)
    try:
        ensure_lite_analysis_history_table()
        with store.connect() as conn:
            conn.execute(
                """INSERT INTO lite_analysis_history
                   (id, username, symbol, stock_name, market, overall_rating, score, analyzed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (record_id, username, symbol, stock_name, market, overall_rating, score, now),
            )
            conn.commit()
    except Exception:
        pass  # history write failure is non-fatal


def _index_report_fts(
    report_id: str,
    symbol: str,
    stock_name: str,
    rating: str,
    content: str,
) -> None:
    try:
        ensure_lite_report_fts_table()
        with store.connect() as conn:
            conn.execute(
                "DELETE FROM lite_report_fts WHERE report_id = ?", (report_id,)
            )
            conn.execute(
                "INSERT INTO lite_report_fts (report_id, symbol, stock_name, rating, content) VALUES (?, ?, ?, ?, ?)",
                (report_id, symbol, stock_name, rating, content),
            )
            conn.commit()
    except Exception:
        pass


def _search_reports_fts(query: str, limit: int = 20) -> list[dict]:
    try:
        ensure_lite_report_fts_table()
        like_pat = f"%{query}%"
        with store.connect() as conn:
            rows = conn.execute(
                """SELECT report_id, symbol, stock_name, rating
                   FROM lite_report_fts
                   WHERE symbol = ?
                      OR stock_name LIKE ?
                      OR content LIKE ?
                   LIMIT ?""",
                (query, like_pat, like_pat, limit),
            ).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        return []
