#!/usr/bin/env bash
# Undo install.sh.
#
#   sudo ./uninstall.sh            stop and remove the screen and web UI services; your
#                                  settings and the screens you made are kept for a reinstall
#   sudo ./uninstall.sh --purge    also delete /opt/lcd-dashboards, /var/lib/lcd-dashboard
#                                  (chosen dashboard, password, screens you made in the
#                                  editor) and the lcd/lcdweb users
#
# Left alone: the apt packages (Pillow, psutil, fonts), which other things may use,
# and the LCD driver in the boot config, so the screen keeps working for other uses.
# To remove the driver too, delete the block marked "lcd-dashboards" from
# /boot/firmware/config.txt (a backup of the original is next to it) and reboot.
set -euo pipefail

PURGE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge) PURGE=1; shift ;;
    --unit) shift 2 ;;                       # accepted for old instructions; found automatically now
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done
[[ $EUID -eq 0 ]] || { echo "Run it with sudo:  sudo $0" >&2; exit 1; }
cd /

say() { printf '==> %s\n' "$*"; }
MARK="# Added by lcd-dashboards install.sh; remove with uninstall.sh"
OLD_MARK="# Added by lcd webui install.sh; remove with uninstall.sh"
ours() { [[ -f "$1" ]] && grep -qxF -e "$MARK" -e "$OLD_MARK" "$1"; }

if ours /etc/systemd/system/lcd-webui.service; then
  say "Removing lcd-webui.service"
  systemctl disable --now -q lcd-webui.service 2>/dev/null || true
  rm -f /etc/systemd/system/lcd-webui.service
fi
if ours /etc/systemd/system/lcd-dashboard.service; then
  say "Removing lcd-dashboard.service"
  systemctl disable --now -q lcd-dashboard.service 2>/dev/null || true
  rm -f /etc/systemd/system/lcd-dashboard.service
fi
RESTORE=()
for f in /etc/systemd/system/*.service.d/10-lcd-runner.conf; do
  ours "$f" || continue
  unit="$(basename "$(dirname "$f")" .d)"
  say "Giving $unit its original command back"
  rm -f "$f"; rmdir "$(dirname "$f")" 2>/dev/null || true
  RESTORE+=("$unit")
done
systemctl daemon-reload
for unit in ${RESTORE[@]+"${RESTORE[@]}"}; do systemctl restart "$unit" || true; done

# the Roboto link install.sh made (only if it is a link, never a real font file)
ROBOTO=/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf
[[ -L "$ROBOTO" ]] && rm -f "$ROBOTO" && rmdir /usr/share/fonts/truetype/roboto 2>/dev/null || true

if [[ $PURGE -eq 1 ]]; then
  n=$(find /var/lib/lcd-dashboard/screens -name '*.json' 2>/dev/null | wc -l || true)
  [[ $n -gt 0 ]] && say "Deleting $n screen(s) you made in the editor"
  if [[ -e /opt/lcd-dashboards/.git ]]; then
    # installed in place from a git clone: that's your copy of the project, keep it
    say "Keeping /opt/lcd-dashboards: it's a git clone, not a copy made by install.sh"
  elif [[ -f /opt/lcd-dashboards/webui/lcd_runner.py ]]; then
    say "Deleting /opt/lcd-dashboards"
    rm -rf /opt/lcd-dashboards
  fi
  say "Deleting /var/lib/lcd-dashboard and the lcd/lcdweb users"
  rm -rf /var/lib/lcd-dashboard
  for u in lcdweb lcd; do id "$u" >/dev/null 2>&1 && userdel "$u"; done
fi
if [[ $PURGE -eq 1 ]]; then say "Removed."
else say "Removed. Your settings and screens are kept in /var/lib/lcd-dashboard (--purge deletes them)."; fi
