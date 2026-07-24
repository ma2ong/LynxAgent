from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASES = (
    ROOT / "runtime" / "quant_data.sqlite",
    ROOT / "runtime" / "lite.sqlite",
)


def checkpoint(database: Path) -> None:
    if not database.exists():
        return

    with sqlite3.connect(database, timeout=30) as connection:
        connection.execute("PRAGMA busy_timeout = 30000")
        result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    print(f"checkpoint {database.name}: {result}")


if __name__ == "__main__":
    for database_path in DATABASES:
        try:
            checkpoint(database_path)
        except sqlite3.Error as error:
            print(f"checkpoint {database_path.name} skipped: {error}")
