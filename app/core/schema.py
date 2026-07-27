"""SaaS Lite 的 SQLite 建表/迁移（从 lite_main 抽出）。

全部 `CREATE TABLE IF NOT EXISTS` + 补列，幂等，可反复调用。抽出来的原因：这些
`ensure_*` 是最底层的共享依赖（缓存、自选、纸面交易、分析历史都要），留在 lite_main
会让任何想用它们的模块都被迫依赖那个 5700 行的 app 模块，进而成环。这里只依赖
`app.lite_auth.store`（不 import lite_main），任何层都能直接 import。
"""
from __future__ import annotations

from app.lite_auth import store


def ensure_lite_favorites_table() -> None:
    with store.connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lite_favorites (
                username TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                stock_name TEXT NOT NULL,
                market TEXT NOT NULL,
                tags_json TEXT NOT NULL DEFAULT '[]',
                notes TEXT,
                added_price REAL,
                added_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (username, stock_code)
            )
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(lite_favorites)").fetchall()}
        if "added_price" not in columns:
            conn.execute("ALTER TABLE lite_favorites ADD COLUMN added_price REAL")
        if "alert_price_high" not in columns:
            conn.execute("ALTER TABLE lite_favorites ADD COLUMN alert_price_high REAL")
        if "alert_price_low" not in columns:
            conn.execute("ALTER TABLE lite_favorites ADD COLUMN alert_price_low REAL")
        conn.commit()


def ensure_lite_news_table() -> None:
    with store.connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lite_news_events (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT,
                source TEXT NOT NULL,
                source_type TEXT NOT NULL,
                event_type TEXT NOT NULL,
                sentiment TEXT NOT NULL,
                sentiment_score REAL NOT NULL,
                importance TEXT NOT NULL,
                catalyst_score REAL NOT NULL,
                symbols_json TEXT NOT NULL DEFAULT '[]',
                stock_names_json TEXT NOT NULL DEFAULT '[]',
                tags_json TEXT NOT NULL DEFAULT '[]',
                url TEXT,
                publish_time TEXT NOT NULL,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lite_news_publish_time ON lite_news_events(publish_time DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lite_news_source_type ON lite_news_events(source_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lite_news_sentiment ON lite_news_events(sentiment)")
        conn.commit()


def ensure_lite_cache_table() -> None:
    with store.connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lite_response_cache (
                cache_key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def ensure_lite_paper_tables() -> None:
    with store.connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lite_paper_accounts (
                username TEXT PRIMARY KEY,
                cash REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lite_paper_positions (
                username TEXT NOT NULL,
                code TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                avg_cost REAL NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (username, code)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lite_paper_orders (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                code TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price REAL NOT NULL,
                amount REAL NOT NULL,
                status TEXT NOT NULL,
                execution_mode TEXT NOT NULL,
                bridge_json TEXT,
                analysis_id TEXT,
                created_at TEXT NOT NULL,
                filled_at TEXT
            )
            """
        )
        conn.commit()


def ensure_lite_analysis_history_table() -> None:
    with store.connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lite_analysis_history (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                symbol TEXT NOT NULL,
                stock_name TEXT,
                market TEXT NOT NULL DEFAULT 'A股',
                overall_rating TEXT,
                score REAL,
                analyzed_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_user ON lite_analysis_history(username, analyzed_at DESC)"
        )
        conn.commit()


def ensure_lite_report_fts_table() -> None:
    with store.connect() as conn:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS lite_report_fts USING fts5(
                report_id UNINDEXED,
                symbol,
                stock_name,
                rating,
                content,
                tokenize='unicode61'
            )
            """
        )
        conn.commit()


def init_all() -> None:
    """启动时建齐所有表。失败只告警不抛：DB 不可写时也让进程起来，探活/静态资源仍可用。"""
    try:
        ensure_lite_favorites_table()
        ensure_lite_news_table()
        ensure_lite_cache_table()
        ensure_lite_paper_tables()
        ensure_lite_analysis_history_table()
        ensure_lite_report_fts_table()
    except Exception as exc:  # noqa: BLE001
        import warnings
        warnings.warn(f"SQLite DB init failed at startup: {exc}", RuntimeWarning, stacklevel=1)
