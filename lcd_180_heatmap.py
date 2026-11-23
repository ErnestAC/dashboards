#!/usr/bin/env python3
"""Pi-hole LCD (480x320): 7-day x 24-hour heatmap of query volume, plus today's numbers and the busiest hour."""
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
font_stat  = ImageFont.truetype(FONT_PATH, 20)

# --- 7-DAY HEATMAP DATA ---
HEAT_REFRESH = 300          # seconds; the week view changes slowly, so cache it
_heat_cache = {"t": 0, "data": None}

def get_heat():
    """Queries per hour for the last 7 local days: (grid[7][24], first_day datetime).
    Row 6 is today. Cached for HEAT_REFRESH seconds."""
    if _heat_cache["data"] and time.time() - _heat_cache["t"] < HEAT_REFRESH:
        return _heat_cache["data"]
    grid = [[0] * 24 for _ in range(7)]
    first = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    first = datetime.fromtimestamp(first.timestamp() - 6 * 86400)
    try:
        start = int(first.timestamp())
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT (timestamp - ?) / 3600, COUNT(*) FROM queries WHERE timestamp >= ? GROUP BY 1;",
                  (start, start))
        for h, cnt in c.fetchall():
            if 0 <= h < 7 * 24: grid[int(h) // 24][int(h) % 24] = cnt
        conn.close()
    except Exception as e:
        print(e)
    _heat_cache.update(t=time.time(), data=(grid, first))
    return grid, first

# --- DRAW ---
BG    = (8, 10, 14)
PANEL = (24, 28, 36)
EMPTY = (34, 39, 50)
FG    = (230, 232, 236)
DIM   = (130, 138, 150)
RED   = (239, 83, 80)
HOT   = (38, 198, 218)      # heat ramp end colour (teal)
GREEN = (102, 187, 106)
AMBER = (255, 183, 77)

HEADER_H, FOOTER_H, M, GAP = 26, 20, 10, 8

def level(v, warn, crit): return RED if v >= crit else AMBER if v >= warn else GREEN

def ramp(t):
    """0..1 -> colour between EMPTY and HOT."""
    t = max(0.0, min(1.0, t))
    return tuple(int(a + (b - a) * t) for a, b in zip(EMPTY, HOT))

def draw_screen(stats):
    cpu, temp = get_cpu(), get_temp()
    ALERT = cpu > 80 or temp > 70
    now = datetime.now()

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # HEADER
    draw.rectangle((0, 0, WIDTH, HEADER_H), fill=RED if ALERT else PANEL)
    if not ALERT: draw.rectangle((0, HEADER_H - 2, WIDTH, HEADER_H), fill=HOT)
    text(draw, M, 3, get_hostname(), FG, font_title)
    right = f"CPU {cpu:.0f}%   {temp:.0f}°C"
    text_right(draw, WIDTH - M, 5, right, FG if ALERT else DIM, font)

    # HEATMAP PANEL
    py0, py1 = HEADER_H + 8, 232
    panel(draw, (M, py0, WIDTH - M, py1), PANEL)
    text(draw, M + 10, py0 + 7, "QUERIES PER HOUR · LAST 7 DAYS", DIM, font_label)

    grid, first = get_heat()
    peak = max(max(r) for r in grid) or 1
    gx0, gy0 = M + 44, py0 + 26
    cw = (WIDTH - M - 10 - gx0) // 24
    ch = 22
    for d in range(7):
        day = datetime.fromtimestamp(first.timestamp() + d * 86400 + 3600)
        label = "Today" if d == 6 else day.strftime("%a")
        text(draw, M + 10, gy0 + d * ch + 4, label, FG if d == 6 else DIM, font_label)
        for h in range(24):
            x, y = gx0 + h * cw, gy0 + d * ch
            future = d == 6 and h > now.hour
            col = BG if future else ramp(grid[d][h] / peak)
            draw.rectangle((x, y, x + cw - 3, y + ch - 3), fill=col)
            if d == 6 and h == now.hour:        # outline the current hour
                draw.rectangle((x - 1, y - 1, x + cw - 2, y + ch - 2), outline=FG)
    for h in (0, 6, 12, 18):
        text(draw, gx0 + h * cw, gy0 + 7 * ch + 1, f"{h:02d}", DIM, font_label)
    # legend
    lx = WIDTH - M - 10
    text_right(draw, lx, py0 + 6, "more", DIM, font_label)
    lx -= text_w(draw, "more", font_label) + 4
    for t in (1, .6, .3, .1, 0):
        draw.rectangle((lx - 9, py0 + 8, lx - 1, py0 + 15), fill=ramp(t)); lx -= 11
    text_right(draw, lx - 2, py0 + 6, "less", DIM, font_label)

    # STAT CARDS: today, blocked rate, busiest hour of the week
    cy0, cy1 = py1 + GAP, HEIGHT - FOOTER_H - 8
    bd, bh = max(((d, h) for d in range(7) for h in range(24)), key=lambda k: grid[k[0]][k[1]])
    busiest = datetime.fromtimestamp(first.timestamp() + bd * 86400 + 3600).strftime("%a") + f" {bh:02d}:00"
    s = stats or {"total": 0, "blocked_pct": 0}
    cards = [("TODAY",        f"{s['total']:,}",          FG),
             ("BLOCKED",      f"{s['blocked_pct']:.1f}%", RED),
             ("BUSIEST HOUR", busiest,                    HOT)]
    cw3 = (WIDTH - 2 * M - 2 * GAP) // 3
    for i, (label, val, col) in enumerate(cards):
        x0 = M + i * (cw3 + GAP)
        panel(draw, (x0, cy0, x0 + cw3, cy1), PANEL)
        text(draw, x0 + 10, cy0 + 6, label, DIM, font_label)
        text(draw, x0 + 10, cy0 + 18, val, col, font_stat)

    # FOOTER
    fy = HEIGHT - FOOTER_H
    draw.rectangle((0, fy, WIDTH, HEIGHT), fill=PANEL)
    text(draw, M, fy + 3, f"IP {get_ip()}", DIM, font_small)
    text_center(draw, WIDTH // 2, fy + 3, f"up {get_uptime_short()}", DIM, font_small)
    text_right(draw, WIDTH - M, fy + 3, now.strftime("%a %d %b  %H:%M"), FG, font_small)
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
