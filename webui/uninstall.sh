#!/usr/bin/env bash
# Undo install.sh: the LCD service goes back to its original command.
#
#   sudo ./uninstall.sh --unit <lcd-service> [--purge]
#
#   --purge  also delete /var/lib/lcd-dashboard and the lcdweb user
set -euo pipefail

UNIT="" PURGE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --unit) UNIT="$2"; shift 2 ;;
    --purge) PURGE=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$UNIT" ]] || { sed -n '2,6p' "$0"; exit 2; }
[[ "$UNIT" == *.service ]] || UNIT="$UNIT.service"
[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }

systemctl disable --now lcd-webui.service 2>/dev/null || true
rm -f /etc/systemd/system/lcd-webui.service
rm -f "/etc/systemd/system/$UNIT.d/10-lcd-runner.conf"
rmdir "/etc/systemd/system/$UNIT.d" 2>/dev/null || true
systemctl daemon-reload
systemctl restart "$UNIT"

if [[ $PURGE -eq 1 ]]; then
  rm -rf /var/lib/lcd-dashboard
  id lcdweb >/dev/null 2>&1 && userdel lcdweb
fi
echo "Removed. $UNIT runs its original command again."
