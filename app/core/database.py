"""MongoDB handle for AStockPick (optional, enrichment-only).

Authentication and favorites live in SQLite (``app/lite_main.py``). Mongo is
used only by the quant router to attach industry/sector metadata to results,
and is entirely optional: if it is not configured the router catches the error
below and serves results without enrichment.

Configuration is read from the environment on first use:
    MONGO_URI   connection string; absent => Mongo disabled
    MONGO_DB    target database name (defaults to "quant")
"""

from __future__ import annotations

import os

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

# Process-wide singletons, created on first call to get_mongo_db().
_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


def get_mongo_db() -> AsyncIOMotorDatabase:
    """Return the shared Mongo database, opening the connection on first use.

    The connection is reused across calls. Raises ``RuntimeError`` when
    MONGO_URI is unset so callers can treat Mongo as an optional feature.
    """
    global _client, _db
    if _db is not None:
        return _db

    uri = os.getenv("MONGO_URI")
    if not uri:
        raise RuntimeError("MONGO_URI not configured; MongoDB features disabled")

    _client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    _db = _client[os.getenv("MONGO_DB", "quant")]
    return _db


async def close_database() -> None:
    """Drop the shared connection if one is open; safe to call unconditionally."""
    global _client, _db
    if _client is not None:
        _client.close()
        _client = None
        _db = None
