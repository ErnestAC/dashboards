#!/usr/bin/env python3
"""Pi-hole LCD (480x320): desk clock. Giant time and date with Pi-hole and system tiles below."""
import time, sqlite3, socket, psutil
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime

# --- CONFIG ---
import os
FB_DEV = os.environ.get("LCD_FB_DEV", "/dev/fb1")
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
        SELECT domain FROM queries
        WHERE status=1 AND timestamp >= strftime('%s','now','-1 day')
        GROUP BY domain
        ORDER BY MAX(timestamp) DESC
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
font_small = ImageFont.truetype(FONT_PATH, 13)
font       = ImageFont.truetype(FONT_PATH, 14)
font_date  = ImageFont.truetype(FONT_PATH, 18)
font_stat  = ImageFont.truetype(FONT_PATH, 22)
font_time  = ImageFont.truetype(FONT_PATH, 124)

# --- DRAW ---
BG    = (8, 10, 14)
PANEL = (24, 28, 36)
TRACK = (48, 54, 66)
FG    = (236, 238, 242)
DIM   = (130, 138, 150)
RED   = (239, 83, 80)
BLUE  = (66, 165, 245)
GREEN = (102, 187, 106)
AMBER = (255, 183, 77)

M = 14

def level(v, warn, crit): return RED if v >= crit else AMBER if v >= warn else GREEN

def draw_screen(stats):
    cpu, temp, mem = get_cpu(), get_temp(), get_mem()
    ALERT = cpu > 80 or temp > 70
    now = datetime.now()

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # date + host
    text(draw, M, 12, now.strftime("%A %d %B"), DIM, font_date)
    if ALERT: text_right(draw, WIDTH - M, 15, "SYSTEM HOT", RED, font)
    else:     text_right(draw, WIDTH - M, 15, get_hostname(), DIM, font)

    # giant clock (baseline-anchored so digit height is stable)
    draw.text((WIDTH // 2, 158), now.strftime("%H:%M"), fill=RED if ALERT else FG,
              font=font_time, anchor="ms")
    text_center(draw, WIDTH // 2, 170, f"up {get_uptime_short()}   ·   {get_ip()}", DIM, font_small)

    # stat tiles
    s = stats or {"blocked_pct": 0, "total": 0, "clients": 0}
    tiles = [
        ("BLOCKED",  f"{s['blocked_pct']:.1f}%", RED),
        ("QUERIES",  f"{s['total']:,}",          FG),
        ("CLIENTS",  f"{s['clients']}",          FG),
        ("CPU",      f"{cpu:.0f}%",              level(cpu, 60, 80)),
        ("TEMP",     f"{temp:.0f}°C",            level(temp, 60, 70)),
    ]
    gap, ty0, ty1 = 6, 204, 262
    tw = (WIDTH - 2 * M - gap * (len(tiles) - 1)) / len(tiles)
    for i, (label, val, col) in enumerate(tiles):
        x0 = int(M + i * (tw + gap)); x1 = int(x0 + tw)
        panel(draw, (x0, ty0, x1, ty1), PANEL)
        text_center(draw, (x0 + x1) // 2, ty0 + 8, label, DIM, font_label)
        text_center(draw, (x0 + x1) // 2, ty0 + 24, val, col, font_stat)

    # blocked / forwarded share across the bottom
    by, bh = 282, 8
    bw = WIDTH - 2 * M
    draw.rectangle((M, by, M + bw, by + bh), fill=TRACK)
    if stats:
        b = int(bw * stats["blocked_pct"] / 100)
        f = int(bw * stats["forwarded_pct"] / 100)
        if b: draw.rectangle((M, by, M + b, by + bh), fill=RED)
        if f: draw.rectangle((M + b, by, min(M + b + f, M + bw), by + bh), fill=BLUE)
        text(draw, M, by + 13, f"{stats['blocked']:,} blocked", RED, font_label)
        text_center(draw, WIDTH // 2, by + 13, f"{stats['forwarded_pct']:.0f}% forwarded", BLUE, font_label)
        text_right(draw, WIDTH - M, by + 13, f"last query {stats['last']}", DIM, font_label)
    return img

# --- FB ---
from PIL import ImageChops

# RGB565 split into its high and low byte, per channel (lookup tables)
_R_HI = [v & 0xF8 for v in range(256)]
_G_HI = [v >> 5 for v in range(256)]
_G_LO = [(v & 0x1C) << 3 for v in range(256)]
_B_LO = [v >> 3 for v in range(256)]
FB_REFRESH = 60          # rewrite an unchanged frame at least this often (seconds)
_last_frame, _last_write = None, 0.0

def write_fb(img):
    """Send img to the framebuffer as little-endian RGB565 in a single write.
    Unchanged frames are skipped (but rewritten every FB_REFRESH seconds)."""
    global _last_frame, _last_write
    r, g, b = img.convert("RGB").split()
    hi = ImageChops.add(r.point(_R_HI), g.point(_G_HI))
    lo = ImageChops.add(g.point(_G_LO), b.point(_B_LO))
    frame = Image.merge("LA", (lo, hi)).tobytes()
    if frame == _last_frame and time.time() - _last_write < FB_REFRESH:
        return
    with open(FB_DEV, "wb") as f:
        f.write(frame)
    _last_frame, _last_write = frame, time.time()

# --- CPU SAVING ---
# Pi-hole numbers change slowly: query the database at most every STATS_TTL
# seconds and reuse the result for the frames in between.
STATS_TTL = 30

def _every(seconds, fn):
    cache = {"t": 0.0, "v": None}
    def wrapper(*args):
        if cache["v"] is None or time.time() - cache["t"] >= seconds:
            cache["v"], cache["t"] = fn(*args), time.time()
        return cache["v"]
    return wrapper

get_stats = _every(STATS_TTL, get_stats)

# --- LOOP ---
if __name__ == "__main__":
    while True:
        write_fb(draw_screen(get_stats()))
        time.sleep(UPDATE_INTERVAL)
