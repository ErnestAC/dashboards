#!/usr/bin/env python3
"""Pi-hole LCD (480x320): headline numbers and a 24-hour stacked bar chart of allowed vs blocked queries."""
import time, sqlite3, socket, psutil
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime

# --- CONFIG ---
FB_DEV = "/dev/fb1"
WIDTH, HEIGHT = 480, 320
DB_PATH = "/etc/pihole/pihole-FTL.db"
UPDATE_INTERVAL = 4

# --- SYSTEM ---
def get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]; s.close(); return ip
    except: return "N/A"

def get_hostname(): return socket.gethostname()

def get_temp():
    try: return int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000
    except: return 0

def get_cpu(): return psutil.cpu_percent()

def get_mem(): return psutil.virtual_memory().percent

def get_uptime_short():
    """'12d 7h' when up for days, otherwise '7h 43m'."""
    try:
        t = int(float(open("/proc/uptime").read().split()[0]))
        d, h, m = t // 86400, (t // 3600) % 24, (t // 60) % 60
        return f"{d}d {h}h" if d else f"{h}h {m}m"
    except: return "N/A"

# --- PIHOLE ---
def get_stats():
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("""
        SELECT COUNT(*),
               SUM(CASE WHEN status=1 THEN 1 ELSE 0 END),
               SUM(CASE WHEN type=1 THEN 1 ELSE 0 END),
               MAX(timestamp)
        FROM queries
        WHERE timestamp >= strftime('%s','now','start of day');
        """)
        total, blocked, forwarded, last = c.fetchone()

        c.execute("SELECT COUNT(DISTINCT client) FROM queries WHERE timestamp >= strftime('%s','now','start of day');")
        clients = c.fetchone()[0]

        c.execute("""
        SELECT DISTINCT domain FROM queries
        WHERE status=1
        ORDER BY timestamp DESC
        LIMIT 7;
        """)
        last_blocked = [row[0] for row in c.fetchall()]
        conn.close()

        return {
            "total": total or 0,
            "blocked": blocked or 0,
            "forwarded": forwarded or 0,
            "blocked_pct": (blocked / total * 100) if total else 0,
            "forwarded_pct": (forwarded / total * 100) if total else 0,
            "clients": clients,
            "last": datetime.fromtimestamp(last).strftime("%H:%M") if last else "N/A",
            "last_blocked": last_blocked,
        }
    except Exception as e:
        print(e); return None

# --- HELPERS ---
def text(draw, x, y, s, c, f): draw.text((x, y), s, fill=c, font=f)

def text_w(draw, s, f): return draw.textbbox((0, 0), s, font=f)[2]

def text_right(draw, x, y, s, c, f): draw.text((x - text_w(draw, s, f), y), s, fill=c, font=f)

def text_center(draw, cx, y, s, c, f): draw.text((cx - text_w(draw, s, f) // 2, y), s, fill=c, font=f)

def fit(draw, s, f, maxw):
    """Trim s with an ellipsis so it fits maxw pixels."""
    if text_w(draw, s, f) <= maxw: return s
    while s and text_w(draw, s + "…", f) > maxw: s = s[:-1]
    return s + "…"

def panel(draw, box, fill, r=6):
    # rounded_rectangle needs Pillow >= 8.2; fall back to square corners
    if hasattr(draw, "rounded_rectangle"): draw.rounded_rectangle(box, radius=r, fill=fill)
    else: draw.rectangle(box, fill=fill)

# --- FONTS ---
FONT_PATH = "/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf"
font_label = ImageFont.truetype(FONT_PATH, 10)
font_small = ImageFont.truetype(FONT_PATH, 12)
font       = ImageFont.truetype(FONT_PATH, 14)
font_title = ImageFont.truetype(FONT_PATH, 16)
font_stat  = ImageFont.truetype(FONT_PATH, 28)

# --- 24H HISTORY ---
def get_hourly():
    """Per-hour (allowed, blocked) counts for the last 24 hours, oldest first.
    Also returns the hour (local) of the first bucket."""
    try:
        now = int(time.time())
        start = (now // 3600 - 23) * 3600
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("""
        SELECT (timestamp - ?) / 3600 AS h,
               SUM(CASE WHEN status=1 THEN 0 ELSE 1 END),
               SUM(CASE WHEN status=1 THEN 1 ELSE 0 END)
        FROM queries WHERE timestamp >= ?
        GROUP BY h;
        """, (start, start))
        buckets = [(0, 0)] * 24
        for h, allowed, blocked in c.fetchall():
            if 0 <= h < 24: buckets[int(h)] = (allowed or 0, blocked or 0)
        conn.close()
        return buckets, datetime.fromtimestamp(start).hour
    except Exception as e:
        print(e); return [(0, 0)] * 24, 0

# --- DRAW ---
BG    = (8, 10, 14)
PANEL = (24, 28, 36)
TRACK = (48, 54, 66)
GRID  = (36, 41, 51)
FG    = (230, 232, 236)
DIM   = (130, 138, 150)
RED   = (239, 83, 80)
BLUE  = (66, 165, 245)
SLATE = (84, 110, 140)
GREEN = (102, 187, 106)
AMBER = (255, 183, 77)

HEADER_H, FOOTER_H, M, GAP = 26, 20, 10, 8

def level(v, warn, crit): return RED if v >= crit else AMBER if v >= warn else GREEN

def stat_card(draw, x0, x1, y0, y1, label, value, color):
    panel(draw, (x0, y0, x1, y1), PANEL)
    text(draw, x0 + 10, y0 + 7, label, DIM, font_label)
    text(draw, x0 + 10, y0 + 18, value, color, font_stat)

def draw_screen(stats):
    cpu, temp, mem = get_cpu(), get_temp(), get_mem()
    ALERT = cpu > 80 or temp > 70

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # HEADER: hostname, inline system readings
    draw.rectangle((0, 0, WIDTH, HEADER_H), fill=RED if ALERT else PANEL)
    text(draw, M, 3, get_hostname(), FG, font_title)
    x = WIDTH - M
    for label, val, col in reversed([
        ("CPU",  f"{cpu:.0f}%",   level(cpu, 60, 80)),
        ("MEM",  f"{mem:.0f}%",   level(mem, 70, 90)),
        ("TEMP", f"{temp:.0f}°C", level(temp, 60, 70)),
    ]):
        vc = FG if ALERT else col
        text_right(draw, x, 5, val, vc, font); x -= text_w(draw, val, font) + 4
        text_right(draw, x, 5, label, FG if ALERT else DIM, font); x -= text_w(draw, label, font) + 14

    top = HEADER_H + 8

    # STAT CARDS
    cw = (WIDTH - 2 * M - 2 * GAP) // 3
    cy1 = top + 54
    s = stats or {"total": 0, "blocked": 0, "blocked_pct": 0}
    for i, (label, val, col) in enumerate([
        ("QUERIES TODAY", f"{s['total']:,}",          FG),
        ("BLOCKED",       f"{s['blocked']:,}",        RED),
        ("BLOCK RATE",    f"{s['blocked_pct']:.1f}%", RED),
    ]):
        x0 = M + i * (cw + GAP)
        stat_card(draw, x0, x0 + cw, top, cy1, label, val, col)

    # 24H CHART
    py0, py1 = cy1 + GAP, HEIGHT - FOOTER_H - 34
    panel(draw, (M, py0, WIDTH - M, py1), PANEL)
    text(draw, M + 10, py0 + 7, "LAST 24 HOURS", DIM, font_label)
    lx = WIDTH - M - 10
    for name, col in (("blocked", RED), ("allowed", SLATE)):
        text_right(draw, lx, py0 + 6, name, DIM, font_label)
        lx -= text_w(draw, name, font_label) + 4
        draw.rectangle((lx - 7, py0 + 8, lx - 1, py0 + 14), fill=col)
        lx -= 18

    buckets, first_hour = get_hourly()
    ch_x0, ch_x1 = M + 10, WIDTH - M - 10
    ch_y0, ch_y1 = py0 + 24, py1 - 18
    peak = max((a + b for a, b in buckets), default=0) or 1
    for frac in (0.5, 1.0):
        gy = int(ch_y1 - (ch_y1 - ch_y0) * frac)
        draw.line((ch_x0, gy, ch_x1, gy), fill=GRID)
    slot = (ch_x1 - ch_x0) / 24
    bw = max(2, int(slot) - 4)
    for i, (allowed, blocked) in enumerate(buckets):
        bx = int(ch_x0 + i * slot + (slot - bw) / 2)
        ha = int((ch_y1 - ch_y0) * allowed / peak)
        hb = int((ch_y1 - ch_y0) * blocked / peak)
        if ha: draw.rectangle((bx, ch_y1 - ha - hb, bx + bw, ch_y1 - hb), fill=SLATE)
        if hb: draw.rectangle((bx, ch_y1 - hb, bx + bw, ch_y1), fill=RED)
        hour = (first_hour + i) % 24
        if hour % 6 == 0:
            text_center(draw, int(bx + bw / 2), ch_y1 + 3, f"{hour:02d}", DIM, font_label)
    text_right(draw, ch_x1, ch_y0 - 2, f"{peak:,}/h", DIM, font_label)

    # LATEST BLOCKED strip
    sy = py1 + 6
    text(draw, M, sy + 2, "LAST BLOCKED", DIM, font_label)
    if stats and stats["last_blocked"]:
        lb = stats["last_blocked"][0]
        text(draw, M + 78, sy, fit(draw, lb, font, 250), FG, font)
    if stats:
        text_right(draw, WIDTH - M, sy, f"{stats['clients']} clients", DIM, font)

    # FOOTER
    fy = HEIGHT - FOOTER_H
    draw.rectangle((0, fy, WIDTH, HEIGHT), fill=PANEL)
    text(draw, M, fy + 3, f"IP {get_ip()}", DIM, font_small)
    text_center(draw, WIDTH // 2, fy + 3, f"up {get_uptime_short()}", DIM, font_small)
    text_right(draw, WIDTH - M, fy + 3, datetime.now().strftime("%a %d %b  %H:%M"), FG, font_small)
    return img

# --- FB ---
def write_fb(img):
    img = img.convert("RGB")
    with open(FB_DEV, "wb") as f:
        for y in range(HEIGHT):
            for x in range(WIDTH):
                r, g, b = img.getpixel((x, y))
                rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                f.write(rgb565.to_bytes(2, "little"))

# --- LOOP ---
if __name__ == "__main__":
    while True:
        write_fb(draw_screen(get_stats()))
        time.sleep(UPDATE_INTERVAL)
