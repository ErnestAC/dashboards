#!/usr/bin/env bash
# Install the LCD dashboard web UI next to an existing LCD service.
#
#   sudo ./install.sh --unit <lcd-service> --default <script.py> [--port 8911]
#
#   --unit     the systemd service that drives the LCD today (e.g. lcd.service)
#   --default  the dashboard it runs today, used until you pick another one
#
# What it changes (nothing belonging to Pi-hole):
#   - creates system user "lcdweb" (no login, no home)
#   - creates /var/lib/lcd-dashboard (state: active script, status, password hash)
#   - adds a drop-in to the LCD service so it runs webui/lcd_runner.py
#     (the original unit file is not edited; uninstall.sh removes the drop-in)
#   - adds and starts lcd-webui.service on the chosen port
# Pi-hole's files, database, web server and services are only read, never changed.
set -euo pipefail

UNIT="" DEFAULT="" PORT=8911
while [[ $# -gt 0 ]]; do
  case "$1" in
    --unit) UNIT="$2"; shift 2 ;;
    --default) DEFAULT="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$UNIT" && -n "$DEFAULT" ]] || { sed -n '2,9p' "$0"; exit 2; }
[[ "$UNIT" == *.service ]] || UNIT="$UNIT.service"
[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }

WEBUI_DIR="$(cd "$(dirname "$0")" && pwd)"
DASH_DIR="$(dirname "$WEBUI_DIR")"
STATE_DIR=/var/lib/lcd-dashboard
PY=/usr/bin/python3
WEB_USER=lcdweb
DB=/etc/pihole/pihole-FTL.db

say()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- checks
say "Checking"
systemctl cat "$UNIT" >/dev/null 2>&1 || fail "no such service: $UNIT"
[[ -f "$DASH_DIR/$DEFAULT" ]] || fail "$DEFAULT not found in $DASH_DIR"
"$PY" -c "import PIL" 2>/dev/null || fail "Pillow is not installed for $PY"
if ss -ltnH "sport = :$PORT" | grep -q .; then
  systemctl is-active -q lcd-webui.service || fail "port $PORT is already in use"
fi

# ------------------------------------------------------------ user, state
if ! id "$WEB_USER" >/dev/null 2>&1; then
  say "Creating system user $WEB_USER"
  useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$WEB_USER"
fi
if ! runuser -u "$WEB_USER" -- test -r "$DASH_DIR/$DEFAULT"; then
  fail "$WEB_USER can't read $DASH_DIR (a private home folder?). Put the dashboards somewhere like /opt/lcd-dashboards and run again."
fi

say "Preparing $STATE_DIR"
install -d -o "$WEB_USER" -g "$WEB_USER" -m 0755 "$STATE_DIR"
LCD_USER="$(systemctl show -p User --value "$UNIT")"
if [[ -n "$LCD_USER" && "$LCD_USER" != root ]]; then
  # the LCD service runs as a normal user: let it write the state folder too
  chgrp "$(id -gn "$LCD_USER")" "$STATE_DIR"; chmod 0775 "$STATE_DIR"
fi
[[ -s "$STATE_DIR/active" ]] || { echo "$DEFAULT" > "$STATE_DIR/active"; chown "$WEB_USER:" "$STATE_DIR/active"; }

if [[ ! -s "$STATE_DIR/webui.passwd" ]]; then
  say "Set the web UI password"
  runuser -u "$WEB_USER" -- env LCD_STATE_DIR="$STATE_DIR" "$PY" "$WEBUI_DIR/lcd_webui.py" set-password
fi

# groups the web UI needs: video for the live screen view, and the
# database's group if Pi-hole's DB isn't world-readable (read access only)
GROUPS_NEEDED=""
getent group video >/dev/null && GROUPS_NEEDED="video"
if [[ -e "$DB" ]] && ! runuser -u "$WEB_USER" -- test -r "$DB"; then
  GROUPS_NEEDED="$GROUPS_NEEDED $(stat -c %G "$DB")"
fi

# ------------------------------------------------------------- services
say "Pointing $UNIT at the runner (drop-in, original unit untouched)"
install -d "/etc/systemd/system/$UNIT.d"
cat > "/etc/systemd/system/$UNIT.d/10-lcd-runner.conf" <<EOF
# Added by lcd webui install.sh; remove with uninstall.sh
[Service]
ExecStart=
ExecStart=$PY $WEBUI_DIR/lcd_runner.py
Environment=LCD_DASH_DIR=$DASH_DIR LCD_STATE_DIR=$STATE_DIR LCD_DEFAULT=$DEFAULT
EOF

say "Installing lcd-webui.service (port $PORT)"
cat > /etc/systemd/system/lcd-webui.service <<EOF
# Added by lcd webui install.sh; remove with uninstall.sh
[Unit]
Description=LCD dashboard web UI (port $PORT)
After=network-online.target
Wants=network-online.target

[Service]
User=$WEB_USER
Group=$WEB_USER
SupplementaryGroups=$GROUPS_NEEDED
Environment=LCD_DASH_DIR=$DASH_DIR LCD_STATE_DIR=$STATE_DIR LCD_WEBUI_PORT=$PORT PYTHONDONTWRITEBYTECODE=1
ExecStart=$PY $WEBUI_DIR/lcd_webui.py
Restart=on-failure
RestartSec=5

# stay out of Pi-hole's way: low priority, capped resources
Nice=10
IOSchedulingClass=idle
CPUQuota=50%
MemoryMax=256M

# sandbox: read-only system, writable state folder only
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$STATE_DIR
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
LockPersonality=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
say "Restarting $UNIT"
systemctl restart "$UNIT"
systemctl enable --now lcd-webui.service
systemctl restart lcd-webui.service

sleep 3
systemctl is-active -q "$UNIT" || fail "$UNIT is not running; see: journalctl -u $UNIT -n 50"
systemctl is-active -q lcd-webui.service || fail "lcd-webui is not running; see: journalctl -u lcd-webui -n 50"
say "Done: http://$(hostname -I | awk '{print $1}'):$PORT"
