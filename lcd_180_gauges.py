#!/usr/bin/env python3
"""Pi-hole LCD (480x320): ring gauges. Big blocked-today ring, CPU/MEM/TEMP rings, latest blocked list."""
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
font_list  = ImageFont.truetype(FONT_PATH, 13)
font       = ImageFont.truetype(FONT_PATH, 14)
font_title = ImageFont.truetype(FONT_PATH, 16)
font_ring  = ImageFont.truetype(FONT_PATH, 15)
font_huge  = ImageFont.truetype(FONT_PATH, 38)

# --- DRAW ---
BG    = (8, 10, 14)
PANEL = (24, 28, 36)
TRACK = (48, 54, 66)
FG    = (230, 232, 236)
DIM   = (130, 138, 150)
RED   = (239, 83, 80)
GREEN = (102, 187, 106)
AMBER = (255, 183, 77)

HEADER_H, FOOTER_H, M, GAP = 26, 20, 10, 8

def level(v, warn, crit): return RED if v >= crit else AMBER if v >= warn else GREEN

def ring(draw, cx, cy, r, w, pct, color):
    """Donut gauge starting at 12 o'clock, clockwise."""
    box = (cx - r, cy - r, cx + r, cy + r)
    draw.ellipse(box, outline=TRACK, width=w)
    pct = max(0, min(100, pct))
    if pct > 0:
        draw.arc(box, -90, -90 + 360 * pct / 100, fill=color, width=w)

def draw_screen(stats):
    cpu, temp, mem = get_cpu(), get_temp(), get_mem()
    ALERT = cpu > 80 or temp > 70

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # HEADER
    draw.rectangle((0, 0, WIDTH, HEADER_H), fill=RED if ALERT else PANEL)
    text(draw, M, 3, get_hostname(), FG, font_title)
    text_right(draw, WIDTH - M, 5, "SYSTEM HOT" if ALERT else "Pi-hole", FG if ALERT else DIM, font)

    top, bottom = HEADER_H + 8, HEIGHT - FOOTER_H - 8

    # LEFT: big blocked ring
    lx1 = 200
    panel(draw, (M, top, lx1, bottom), PANEL)
    cx, cy, r = (M + lx1) // 2, top + 88, 72
    text(draw, M + 10, top + 7, "BLOCKED TODAY", DIM, font_label)
    if stats:
        ring(draw, cx, cy, r, 14, stats["blocked_pct"], RED)
        text_center(draw, cx, cy - 26, f"{stats['blocked_pct']:.1f}", FG, font_huge)
        text_center(draw, cx, cy + 16, "% blocked", DIM, font_small)
        text_center(draw, cx, cy + r + 12, f"{stats['blocked']:,} of {stats['total']:,}", FG, font_list)
        text_center(draw, cx, cy + r + 32, f"{stats['clients']} clients · last {stats['last']}", DIM, font_small)
    else:
        ring(draw, cx, cy, r, 14, 0, RED)
        text_center(draw, cx, cy - 10, "no data", DIM, font)

    # RIGHT TOP: three system rings
    rx0, rx1 = lx1 + GAP, WIDTH - M
    gy1 = top + 96
    panel(draw, (rx0, top, rx1, gy1), PANEL)
    cw = (rx1 - rx0) // 3
    for i, (label, val, pct, col) in enumerate([
        ("CPU",  f"{cpu:.0f}%",  cpu,             level(cpu, 60, 80)),
        ("MEM",  f"{mem:.0f}%",  mem,             level(mem, 70, 90)),
        ("TEMP", f"{temp:.0f}°", temp / 85 * 100, level(temp, 60, 70)),
    ]):
        gx = rx0 + cw * i + cw // 2
        ring(draw, gx, top + 40, 28, 7, pct, col)
        text_center(draw, gx, top + 31, val, FG, font_ring)
        text_center(draw, gx, top + 76, label, DIM, font_label)

    # RIGHT BOTTOM: latest blocked
    ly0 = gy1 + GAP
    panel(draw, (rx0, ly0, rx1, bottom), PANEL)
    text(draw, rx0 + 10, ly0 + 7, "LATEST BLOCKED", DIM, font_label)
    if stats:
        y = ly0 + 24
        for d in stats["last_blocked"][:6]:
            if y > bottom - 18: break
            draw.ellipse((rx0 + 11, y + 6, rx0 + 16, y + 11), fill=RED)
            text(draw, rx0 + 24, y, fit(draw, d, font_list, rx1 - rx0 - 34), FG, font_list)
            y += 20

    # FOOTER: IP, uptime, clock
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
