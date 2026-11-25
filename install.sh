#!/usr/bin/env bash
# Install the Pi-hole LCD dashboards and their web UI on a Raspberry Pi running Pi-hole.
#
#   sudo ./install.sh                 first install, or update after a git pull
#
# Options (all optional):
#   --port N          web UI port (default 8911)
#   --default FILE    dashboard to show until you pick one in the web UI (default lcd_180.py)
#   --rotate DEG      screen rotation used if the display driver gets set up (default 90)
#   --overlay LINE    exact dtoverlay line for your panel, instead of the PiScreen one
#   --no-display      never touch the boot config, even if no LCD is found
#   --unit NAME       take over an LCD service you already run, instead of adding one
#   --yes             don't ask; accept the defaults
#
# What it does:
#   1. installs Python Pillow, psutil and the fonts the dashboards use (apt)
#   2. copies this folder to /opt/lcd-dashboards (left in place if run from there)
#   3. finds the 3.5" SPI screen; if there is none, offers to enable its driver
#      in the boot config (a reboot is needed after that)
#   4. adds lcd-dashboard.service (draws the screen) and lcd-webui.service (the page
#      where you choose a dashboard or design your own), then asks for a web UI password
# Your settings live in /var/lib/lcd-dashboard: the chosen dashboard, the password
# hash and the screens you make in the editor (screens/). Running this again keeps them.
# Pi-hole's own files, settings and services are never changed; the screen only
# reads Pi-hole's query database. Undo everything with: sudo ./uninstall.sh
set -euo pipefail

ARGS="$*"
PORT=8911 DEFAULT=lcd_180.py ROTATE=90 ROTATE_SET=0 OVERLAY="" NO_DISPLAY=0 UNIT="" YES=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --default) DEFAULT="$2"; shift 2 ;;
    --rotate) ROTATE="$2"; ROTATE_SET=1; shift 2 ;;
    --overlay) OVERLAY="$2"; shift 2 ;;
    --no-display) NO_DISPLAY=1; shift ;;
    --unit) UNIT="$2"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

SRC="$(cd "$(dirname "$0")" && pwd -P)"
cd /                                 # service users may not be able to read the caller's folder
DEST=/opt/lcd-dashboards
STATE_DIR=/var/lib/lcd-dashboard
PIHOLE_DB=/etc/pihole/pihole-FTL.db
PY=/usr/bin/python3
WEB_USER=lcdweb LCD_USER=lcd
OUR_UNIT=lcd-dashboard.service
MARK="# Added by lcd-dashboards install.sh; remove with uninstall.sh"
ROBOTO=/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf
REBOOT=0

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }
ask()  {  # ask "question" -> 0 for yes; --yes answers yes, no terminal answers no
  [[ $YES -eq 1 ]] && return 0
  [[ -t 0 ]] || return 1
  local a; read -r -p "    $1 [Y/n] " a; [[ -z "$a" || "$a" =~ ^[Yy] ]]
}

[[ $EUID -eq 0 ]] || fail "run it with sudo:  sudo $0 $ARGS"
[[ "$PORT" =~ ^[0-9]+$ ]] || fail "--port must be a number"
[[ "$ROTATE" =~ ^(0|90|180|270)$ ]] || fail "--rotate must be 0, 90, 180 or 270"
[[ -f "$SRC/$DEFAULT" ]] || fail "--default: no dashboard called $DEFAULT in $SRC"
[[ -z "$UNIT" || "$UNIT" == *.service ]] || UNIT="$UNIT.service"
if [[ -z "$UNIT" ]]; then            # an earlier install took over an existing LCD service: keep using it
  for f in /etc/systemd/system/*.service.d/10-lcd-runner.conf; do
    [[ -f "$f" ]] && { UNIT="$(basename "$(dirname "$f")" .d)"; break; }
  done
fi

# ------------------------------------------------------------------ checks
say "Checking this is a Pi-hole"
command -v systemctl >/dev/null || fail "systemd is required"
if [[ ! -f "$PIHOLE_DB" ]]; then
  fail "Pi-hole's database ($PIHOLE_DB) is missing. Install Pi-hole first:
    curl -sSL https://install.pi-hole.net | bash"
fi
note "found $PIHOLE_DB"
if ss -ltnH "sport = :$PORT" 2>/dev/null | grep -q . && ! systemctl is-active -q lcd-webui.service; then
  fail "port $PORT is already in use; pick another one with --port"
fi
[[ -z "$UNIT" ]] || systemctl cat "$UNIT" >/dev/null 2>&1 || fail "no such service: $UNIT"

# ------------------------------------------------------------ dependencies
say "Installing Python libraries and fonts"
PKGS=(python3 python3-pil python3-psutil fonts-dejavu-core fonts-roboto-unhinted)
MISSING=()
for p in "${PKGS[@]}"; do
  dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "ok installed" || MISSING+=("$p")
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
  note "apt-get install ${MISSING[*]}"
  export DEBIAN_FRONTEND=noninteractive
  apt-get install -y -q --no-install-recommends "${MISSING[@]}" >/dev/null 2>&1 || {
    note "refreshing the package lists first"
    apt-get update -q >/dev/null
    apt-get install -y -q --no-install-recommends "${MISSING[@]}" >/dev/null
  }
else
  note "already installed"
fi
# The dashboards ask for Roboto at the path older Raspberry Pi OS used; Debian's
# package puts it elsewhere, so link it into place.
if [[ ! -e "$ROBOTO" ]]; then
  found="$(find /usr/share/fonts -name Roboto-Bold.ttf -print -quit 2>/dev/null || true)"
  [[ -n "$found" ]] || fail "Roboto-Bold.ttf not found even after installing fonts-roboto-unhinted"
  install -d "$(dirname "$ROBOTO")"
  ln -s "$found" "$ROBOTO"
  note "linked $ROBOTO -> $found"
fi

# -------------------------------------------------------------------- copy
if [[ "$SRC" != "$DEST" ]]; then
  say "Copying the dashboards to $DEST"
  if [[ -e "$DEST" && ! -f "$DEST/webui/lcd_runner.py" ]]; then
    fail "$DEST exists and isn't an earlier copy of this project; move it away first"
  fi
  rm -rf "$DEST.new"
  install -d -m 0755 "$DEST.new"
  tar -C "$SRC" --exclude=.git --exclude=__pycache__ -cf - . | tar -C "$DEST.new" -xf - --no-same-owner
  chmod -R u=rwX,go=rX "$DEST.new"
  rm -rf "$DEST"; mv "$DEST.new" "$DEST"
  note "$(ls "$DEST"/*.py | wc -l) dashboards (to update later: git pull, then run this again)"
fi
WEBUI_DIR="$DEST/webui"

# ----------------------------------------------------------------- display
# The LCD is the framebuffer made by an SPI display driver (fbtft names them
# fb_<chip>, e.g. fb_ili9486); the HDMI one is vc4/simple and is skipped.
find_panel() {
  local d n
  for d in /sys/class/graphics/fb[0-9]*; do
    [[ -r "$d/name" ]] || continue
    n="$(cat "$d/name")"
    [[ "$n" == fb_* ]] && { echo "/dev/$(basename "$d") $n $(cat "$d/virtual_size" 2>/dev/null)"; return 0; }
  done
  return 1
}

CONFIG=/boot/firmware/config.txt
[[ -f $CONFIG ]] || CONFIG=/boot/config.txt
LINE="${OVERLAY:-dtoverlay=piscreen,speed=16000000,rotate=$ROTATE}"
[[ "$LINE" == dtoverlay=* ]] || LINE="dtoverlay=$LINE"
write_overlay() {  # (re)write our block at the end of the boot config: marker, spi, overlay
  [[ -f "$CONFIG.before-lcd-dashboards" ]] || cp "$CONFIG" "$CONFIG.before-lcd-dashboards"
  awk -v m="$MARK" '$0 == m {skip = 4} skip > 0 {skip--; next} {print}' "$CONFIG" > "$CONFIG.tmp"
  # [all] so the lines apply on every Pi model, whatever section the file ended in
  { echo "$MARK"; echo "[all]"; echo "dtparam=spi=on"; echo "$LINE"; } >> "$CONFIG.tmp"
  mv "$CONFIG.tmp" "$CONFIG"
  note "$CONFIG now has: $LINE  (backup: $CONFIG.before-lcd-dashboards)"
  REBOOT=1
}

say "Looking for the LCD"
FB_DEV=/dev/fb1
if [[ -f $CONFIG ]] && grep -qxF "$MARK" "$CONFIG" && [[ $ROTATE_SET -eq 1 || -n "$OVERLAY" ]]; then
  grep -qxF "$LINE" "$CONFIG" || write_overlay      # changing the driver line set up earlier
fi
if panel="$(find_panel)"; then
  read -r FB_DEV chip size <<<"$panel"
  note "found $chip on $FB_DEV (${size/,/x})"
  [[ "$size" == "480,320" ]] || warn "the dashboards are 480x320 but the screen is ${size/,/x}; try --rotate 90 or 270"
elif [[ -e /dev/fb1 ]]; then
  note "using /dev/fb1 (driver not recognised, assuming it is the LCD)"
elif [[ $NO_DISPLAY -eq 1 ]]; then
  warn "no LCD found; set up its driver, reboot, then run this again"
elif [[ $REBOOT -eq 0 ]]; then
  [[ -f $CONFIG ]] || fail "no LCD found and no boot config to enable one in; use --no-display"
  if grep -qE '^\s*dtoverlay=(piscreen|tft35a|waveshare35|mhs35|pitft35)' "$CONFIG"; then
    note "a display driver is already in $CONFIG but no screen appeared yet"
    note "reboot once; if it still isn't found, check the line in $CONFIG"
    REBOOT=1
  else
    echo "    No LCD found. The usual 3.5\" 480x320 SPI screens (ILI9486, \"LCD35\")"
    echo "    work with the kernel's PiScreen driver. This adds to $CONFIG:"
    echo "        dtparam=spi=on"
    echo "        $LINE"
    if ask "Enable the LCD driver now?"; then
      write_overlay
    else
      warn "skipped; the screen will stay blank until its driver is set up"
    fi
  fi
fi

# ------------------------------------------------------------ users, state
say "Setting up users and $STATE_DIR"
mkuser() {
  id "$1" >/dev/null 2>&1 && return 0
  useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$1"
  note "created system user $1"
}
mkuser "$WEB_USER"
GROUPS_WEB=""; getent group video >/dev/null && GROUPS_WEB=video

if [[ -z "$UNIT" ]]; then
  # the screen runs as its own user, with access to the framebuffer (video) and,
  # if needed, Pi-hole's database (pihole group)
  mkuser "$LCD_USER"
  GROUPS_LCD=""
  for g in video pihole; do getent group "$g" >/dev/null && GROUPS_LCD+=" $g"; done
  GROUPS_LCD="${GROUPS_LCD# }"
  [[ -n "$GROUPS_LCD" ]] && usermod -a -G "${GROUPS_LCD// /,}" "$LCD_USER"
  RUN_AS="$LCD_USER"
  # reading the schema proves access and is instant; counting queries can take
  # minutes on a big database. The timeout guards against a long lock.
  if ! timeout 20 runuser -u "$LCD_USER" -- "$PY" -c "
import sqlite3; c = sqlite3.connect('$PIHOLE_DB', timeout=10); c.execute('SELECT COUNT(*) FROM sqlite_master').fetchone()
" >/dev/null 2>&1; then
    warn "user $LCD_USER can't read Pi-hole's database; the screen will run as root instead"
    RUN_AS=root
  fi
else
  RUN_AS="$(systemctl show -p User --value "$UNIT")"; RUN_AS="${RUN_AS:-root}"
fi

install -d -o "$WEB_USER" -g "$WEB_USER" -m 0755 "$STATE_DIR"
if [[ "$RUN_AS" != root ]]; then      # the screen writes its status here too
  chgrp "$(id -gn "$RUN_AS")" "$STATE_DIR"; chmod 0775 "$STATE_DIR"
fi
install -d -o "$WEB_USER" -g "$WEB_USER" -m 0755 "$STATE_DIR/screens"   # screens made in the editor
[[ -s "$STATE_DIR/active" ]] || { echo "$DEFAULT" > "$STATE_DIR/active"; chown "$WEB_USER:" "$STATE_DIR/active"; }

if [[ ! -s "$STATE_DIR/webui.passwd" ]]; then
  say "Choose a password for the web UI (at least 8 characters)"
  until runuser -u "$WEB_USER" -- env LCD_STATE_DIR="$STATE_DIR" "$PY" "$WEBUI_DIR/lcd_webui.py" set-password; do
    [[ -t 0 ]] || fail "no terminal to ask for a password; run: sudo -u $WEB_USER LCD_STATE_DIR=$STATE_DIR $PY $WEBUI_DIR/lcd_webui.py set-password"
    echo "    Let's try that again."
  done
fi

# ---------------------------------------------------------------- services
# LCD_FB_DEV=auto: the runner and web UI find the screen at start, so it works after a reboot
# even if the screen moved between /dev/fb0 and /dev/fb1 (it does when HDMI is unplugged)
ENV_COMMON="LCD_DASH_DIR=$DEST LCD_STATE_DIR=$STATE_DIR LCD_FB_DEV=auto PYTHONDONTWRITEBYTECODE=1"

if [[ -z "$UNIT" ]]; then
  say "Installing $OUR_UNIT (draws the screen)"
  cat > "/etc/systemd/system/$OUR_UNIT" <<EOF
$MARK
[Unit]
Description=Pi-hole LCD dashboard
After=pihole-FTL.service
Wants=pihole-FTL.service

[Service]
User=$RUN_AS
SupplementaryGroups=${GROUPS_LCD:-}
Environment=$ENV_COMMON LCD_DEFAULT=$DEFAULT
# hide the blinking console cursor in case the console also lands on the LCD
ExecStartPre=-+/bin/sh -c 'echo 0 > /sys/class/graphics/fbcon/cursor_blink'
ExecStart=$PY $WEBUI_DIR/lcd_runner.py
Restart=always
RestartSec=5
Nice=10

[Install]
WantedBy=multi-user.target
EOF
  LCD_SERVICE=$OUR_UNIT
else
  say "Pointing $UNIT at the dashboard runner (drop-in; the unit file itself is untouched)"
  install -d "/etc/systemd/system/$UNIT.d"
  cat > "/etc/systemd/system/$UNIT.d/10-lcd-runner.conf" <<EOF
$MARK
[Service]
ExecStart=
ExecStart=$PY $WEBUI_DIR/lcd_runner.py
Environment=$ENV_COMMON LCD_DEFAULT=$DEFAULT
EOF
  LCD_SERVICE=$UNIT
fi

say "Installing lcd-webui.service (the web page, port $PORT)"
cat > /etc/systemd/system/lcd-webui.service <<EOF
$MARK
[Unit]
Description=LCD dashboard web UI (port $PORT)
After=network-online.target
Wants=network-online.target

[Service]
User=$WEB_USER
Group=$WEB_USER
SupplementaryGroups=$GROUPS_WEB
Environment=$ENV_COMMON LCD_WEBUI_PORT=$PORT
ExecStart=$PY $WEBUI_DIR/lcd_webui.py
Restart=on-failure
RestartSec=5

# stay out of Pi-hole's way: low priority, capped resources
Nice=10
IOSchedulingClass=idle
CPUQuota=20%
MemoryMax=128M

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
systemctl enable -q "$LCD_SERVICE" lcd-webui.service
systemctl restart "$LCD_SERVICE" lcd-webui.service

sleep 3
systemctl is-active -q lcd-webui.service || fail "the web UI didn't start; see: journalctl -u lcd-webui -n 50"
if [[ $REBOOT -eq 0 ]] && ! systemctl is-active -q "$LCD_SERVICE"; then
  fail "the screen service didn't start; see: journalctl -u ${LCD_SERVICE%.service} -n 50"
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
say "Done"
echo "    Web UI:   http://${IP:-<pi-address>}:$PORT   (pick a dashboard and Apply, or design your own)"
echo "    Screen:   $LCD_SERVICE, showing $(cat "$STATE_DIR/active")"
echo "    Logs:     journalctl -u ${LCD_SERVICE%.service} -u lcd-webui -f"
echo "    Password: sudo -u $WEB_USER LCD_STATE_DIR=$STATE_DIR $PY $WEBUI_DIR/lcd_webui.py set-password"
echo "    Remove:   sudo $DEST/uninstall.sh"
if [[ $REBOOT -eq 1 ]]; then
  echo
  printf '\033[1;33m    Reboot to turn the LCD on:  sudo reboot\033[0m\n'
  echo "    (if the picture is sideways or upside down, run this again with --rotate 270, 0 or 180)"
fi
