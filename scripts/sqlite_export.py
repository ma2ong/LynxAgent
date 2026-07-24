from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


DATABASES = ("quant_data.sqlite", "lite.sqlite")


def export_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()

    with sqlite3.connect(source, timeout=60) as source_connection:
        source_connection.execute("PRAGMA busy_timeout = 60000")
        with sqlite3.connect(destination, timeout=60) as destination_connection:
            source_connection.backup(destination_connection)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    for database_name in DATABASES:
        source = args.runtime / database_name
        if source.exists():
            export_database(source, args.output / database_name)
            print(f"exported {database_name}")


if __name__ == "__main__":
    main()
