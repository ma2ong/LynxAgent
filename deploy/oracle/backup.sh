#!/usr/bin/env bash
set -euo pipefail

runtime_dir="/opt/astockpick/shared/runtime"
backup_dir="/opt/astockpick/shared/backups/$(date +%F)"

mkdir -p "$backup_dir"

for database in quant_data.sqlite lite.sqlite; do
    source_path="$runtime_dir/$database"
    if [[ ! -f "$source_path" ]]; then
        continue
    fi

    destination="$backup_dir/$database"
    sqlite3 "$source_path" ".timeout 30000" ".backup '$destination'"
    gzip -f "$destination"
done

find /opt/astockpick/shared/backups \
    -mindepth 1 \
    -maxdepth 1 \
    -type d \
    -mtime +7 \
    -exec rm -rf -- {} +
