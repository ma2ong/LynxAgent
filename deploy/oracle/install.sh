#!/usr/bin/env bash
set -euo pipefail

release_archive="${1:-/tmp/astockpick-release.tar.gz}"
environment_file="${2:-/tmp/astockpick.env}"
data_archive="${3:-/tmp/astockpick-data.tar.gz}"
app_root="/opt/astockpick"
release_id="$(date +%Y%m%d%H%M%S)"
release_dir="$app_root/releases/$release_id"

if [[ ! -f "$release_archive" ]]; then
    echo "Release archive not found: $release_archive" >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
    build-essential \
    curl \
    libgomp1 \
    nginx \
    python3 \
    python3-dev \
    python3-venv \
    sqlite3 \
    ufw

if ! id astockpick >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "$app_root" --shell /usr/sbin/nologin astockpick
fi

mkdir -p \
    "$app_root/releases" \
    "$app_root/shared/runtime" \
    "$app_root/shared/backups" \
    "$release_dir"

tar -xzf "$release_archive" -C "$release_dir"

if [[ ! -f "$release_dir/frontend/dist/index.html" ]]; then
    echo "frontend/dist/index.html is missing from the release" >&2
    exit 1
fi

if [[ -f "$data_archive" ]]; then
    systemctl stop astockpick.service 2>/dev/null || true
    tar -xzf "$data_archive" -C "$app_root/shared/runtime"
fi

rm -rf "$release_dir/runtime"
ln -s "$app_root/shared/runtime" "$release_dir/runtime"

if [[ -f "$environment_file" ]]; then
    install -m 600 -o astockpick -g astockpick "$environment_file" /etc/astockpick.env
fi

if [[ ! -d "$app_root/venv" ]]; then
    python3 -m venv "$app_root/venv"
fi

"$app_root/venv/bin/pip" install --upgrade pip wheel setuptools
"$app_root/venv/bin/pip" install -r "$release_dir/deploy/oracle/requirements-extra.txt"
"$app_root/venv/bin/pip" install "$release_dir"

chown -R astockpick:astockpick "$app_root"
chmod +x "$release_dir/deploy/oracle/backup.sh"

ln -sfn "$release_dir" "$app_root/current"

install -m 644 "$release_dir/deploy/oracle/astockpick.service" /etc/systemd/system/astockpick.service
install -m 644 "$release_dir/deploy/oracle/astockpick-backup.service" /etc/systemd/system/astockpick-backup.service
install -m 644 "$release_dir/deploy/oracle/astockpick-backup.timer" /etc/systemd/system/astockpick-backup.timer
install -m 644 "$release_dir/deploy/oracle/nginx.conf" /etc/nginx/sites-available/astockpick

rm -f /etc/nginx/sites-enabled/default
ln -sfn /etc/nginx/sites-available/astockpick /etc/nginx/sites-enabled/astockpick

nginx -t
systemctl daemon-reload
systemctl enable --now astockpick.service
systemctl enable --now astockpick-backup.timer
systemctl enable --now nginx

ufw allow OpenSSH
ufw allow "Nginx Full"
ufw --force enable

healthy=false
for _ in $(seq 1 60); do
    if curl --fail --silent http://127.0.0.1:8001/api/health | grep -q '"healthy"'; then
        healthy=true
        break
    fi
    sleep 2
done

if [[ "$healthy" != "true" ]]; then
    journalctl -u astockpick.service --no-pager -n 100
    exit 1
fi

systemctl reload nginx
rm -f "$release_archive" "$environment_file" "$data_archive"

echo "AStockPick deployment is healthy."
