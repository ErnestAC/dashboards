#!/usr/bin/env python3
"""Pi-hole LCD (480x320): who's online. A tile per device active in the last 15 min: queries, blocked share, last seen."""
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
font_small = ImageFont.truetype(FONT_PATH, 12)
font       = ImageFont.truetype(FONT_PATH, 14)
font_title = ImageFont.truetype(FONT_PATH, 16)
font_num   = ImageFont.truetype(FONT_PATH, 22)

# --- ACTIVE DEVICES ---
WINDOW = 15 * 60
GRID_C, GRID_R = 3, 4

def get_devices():
    """Devices seen in the last WINDOW seconds: [(name, queries, blocked, last_ts)], busiest first."""
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("""
        SELECT client, COUNT(*), SUM(CASE WHEN status=1 THEN 1 ELSE 0 END), MAX(timestamp)
        FROM queries WHERE timestamp >= ?
        GROUP BY client ORDER BY 2 DESC;
        """, (int(time.time()) - WINDOW,))
        rows = c.fetchall()
        names = {}
        try:
            c.execute("SELECT ip, name FROM client_by_id WHERE name IS NOT NULL AND name != '';")
            names = dict(c.fetchall())
        except sqlite3.Error:
            pass
        conn.close()
        return [(names.get(ip, ip), n, b or 0, ts) for ip, n, b, ts in rows]
    except Exception as e:
        print(e); return []

# --- DRAW ---
BG    = (10, 12, 16)
PANEL = (24, 28, 36)
TRACK = (40, 46, 58)
FG    = (230, 232, 236)
DIM   = (130, 138, 150)
RED   = (239, 83, 80)
GREEN = (102, 187, 106)
AMBER = (255, 183, 77)
GREY  = (90, 96, 108)
HEADER_H, FOOTER_H, M, G = 26, 22, 8, 6

def ago(ts):
    s = max(0, int(time.time() - ts))
    return "now" if s < 60 else f"{s // 60}m ago"

def draw_screen(stats):
    cpu, temp = get_cpu(), get_temp()
    ALERT = cpu > 80 or temp > 70
    devices = get_devices()

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    draw.rectangle((0, 0, WIDTH, HEADER_H), fill=RED if ALERT else PANEL)
    text(draw, M, 4, get_hostname(), FG, font_title)
    text_center(draw, WIDTH // 2, 6, f"{len(devices)} ONLINE · LAST 15 MIN", FG if ALERT else DIM, font_small)
    text_right(draw, WIDTH - M, 5, datetime.now().strftime("%H:%M"), FG if ALERT else DIM, font)

    top = HEADER_H + G
    tw = (WIDTH - 2 * M - (GRID_C - 1) * G) // GRID_C
    th = (HEIGHT - top - FOOTER_H - G - (GRID_R - 1) * G) // GRID_R
    shown = devices[:GRID_C * GRID_R]
    if len(devices) > len(shown):                    # last tile says how many more
        shown = shown[:-1]
    for i in range(GRID_C * GRID_R):
        x = M + (i % GRID_C) * (tw + G)
        y = top + (i // GRID_C) * (th + G)
        if i < len(shown):
            name, n, b, ts = shown[i]
            age = time.time() - ts
            dot = GREEN if age < 120 else AMBER if age < 600 else GREY
            panel(draw, (x, y, x + tw, y + th), PANEL)
            draw.ellipse((x + 8, y + 9, x + 15, y + 16), fill=dot)
            text(draw, x + 20, y + 5, fit(draw, name, font_small, tw - 28), FG, font_small)
            text(draw, x + 8, y + 21, f"{n:,}", FG, font_num)
            text_right(draw, x + tw - 8, y + 30, ago(ts), DIM, font_label)
            pct = b / n if n else 0
            bx0, bx1, by = x + 8, x + tw - 8, y + th - 9
            draw.rectangle((bx0, by, bx1, by + 3), fill=TRACK)
            if b:
                draw.rectangle((bx0, by, bx0 + max(2, int((bx1 - bx0) * pct)), by + 3), fill=RED)
        elif i == len(shown) and len(devices) > len(shown):
            panel(draw, (x, y, x + tw, y + th), PANEL)
            text_center(draw, x + tw // 2, y + th // 2 - 8, f"+{len(devices) - len(shown)} more", DIM, font)
        else:
            draw.rectangle((x, y, x + tw, y + th), outline=PANEL)
    if not devices:
        text_center(draw, WIDTH // 2, HEIGHT // 2 - 10, "no devices in the last 15 minutes", DIM, font)

    fy = HEIGHT - FOOTER_H + 4
    if stats:
        text(draw, M, fy, f"Today  {stats['total']:,} queries · {stats['blocked_pct']:.1f}% blocked · "
             f"{stats['clients']} devices", DIM, font_small)
    text_right(draw, WIDTH - M, fy, f"{temp:.0f}°C · CPU {cpu:.0f}%", RED if ALERT else DIM, font_small)
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
get_devices = _every(STATS_TTL, get_devices)

# --- LOOP ---
if __name__ == "__main__":
    while True:
        write_fb(draw_screen(get_stats()))
        time.sleep(UPDATE_INTERVAL)
