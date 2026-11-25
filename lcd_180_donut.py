#!/usr/bin/env python3
"""Pi-hole LCD (480x320): donut chart of how today's queries were answered (blocked, forwarded, cached, other)."""
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
font_big   = ImageFont.truetype(FONT_PATH, 26)

# --- QUERY BREAKDOWN ---
# Pi-hole status codes: every kind of block counts as blocked here
BLOCKED_STATUS = {1, 4, 5, 6, 7, 8, 9, 10, 11, 15, 16}
FORWARDED_STATUS = {2}
CACHED_STATUS = {3, 14}

def get_breakdown():
    """Today's queries split into blocked / forwarded / cached / other."""
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("""
        SELECT status, COUNT(*) FROM queries
        WHERE timestamp >= strftime('%s','now','start of day')
        GROUP BY status;
        """)
        out = {"blocked": 0, "forwarded": 0, "cached": 0, "other": 0}
        for status, n in c.fetchall():
            key = ("blocked" if status in BLOCKED_STATUS else
                   "forwarded" if status in FORWARDED_STATUS else
                   "cached" if status in CACHED_STATUS else "other")
            out[key] += n
        conn.close()
        return out
    except Exception as e:
        print(e); return None

# --- DRAW ---
BG    = (8, 10, 14)
PANEL = (24, 28, 36)
TRACK = (40, 46, 58)
FG    = (230, 232, 236)
DIM   = (130, 138, 150)
RED   = (239, 83, 80)
BLUE  = (66, 165, 245)
TEAL  = (38, 198, 170)
GREY  = (110, 118, 132)
HEADER_H, FOOTER_H, M = 26, 20, 10
SEGMENTS = [("blocked", "Blocked", RED), ("forwarded", "Forwarded", BLUE),
            ("cached", "From cache", TEAL), ("other", "Other", GREY)]

def draw_screen(stats):
    cpu, temp = get_cpu(), get_temp()
    ALERT = cpu > 80 or temp > 70

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # HEADER
    draw.rectangle((0, 0, WIDTH, HEADER_H), fill=RED if ALERT else PANEL)
    text(draw, M, 3, get_hostname(), FG, font_title)
    text_right(draw, WIDTH - M, 5, "HOW TODAY'S QUERIES WERE ANSWERED", FG if ALERT else DIM, font_label)

    data = get_breakdown() or {}
    total = sum(data.values())

    # DONUT
    cx, cy, r, w = 132, 168, 112, 34
    box = (cx - r, cy - r, cx + r, cy + r)
    if total:
        start = -90.0
        for key, _, col in SEGMENTS:
            sweep = 360 * data[key] / total
            if sweep > 0:
                draw.pieslice(box, start, start + sweep, fill=col)
                start += sweep
    else:
        draw.ellipse(box, fill=TRACK)
    draw.ellipse((cx - r + w, cy - r + w, cx + r - w, cy + r - w), fill=BG)
    draw.text((cx, cy + 6), f"{total:,}", fill=FG, font=font_big, anchor="ms")
    draw.text((cx, cy + 24), "QUERIES TODAY", fill=DIM, font=font_label, anchor="ms")

    # LEGEND
    lx0, lx1, y = 268, WIDTH - M, HEADER_H + 22
    for key, label, col in SEGMENTS:
        n = data.get(key, 0)
        pct = n / total * 100 if total else 0
        panel(draw, (lx0, y, lx1, y + 50), PANEL)
        draw.rectangle((lx0, y, lx0 + 5, y + 50), fill=col)
        text(draw, lx0 + 16, y + 8, label.upper(), DIM, font_label)
        text(draw, lx0 + 16, y + 22, f"{n:,}", FG, font)
        text_right(draw, lx1 - 12, y + 16, f"{pct:.1f}%", col, font_title)
        y += 58

    # FOOTER
    fy = HEIGHT - FOOTER_H
    draw.rectangle((0, fy, WIDTH, HEIGHT), fill=PANEL)
    text(draw, M, fy + 3, f"IP {get_ip()}", DIM, font_small)
    text_center(draw, WIDTH // 2, fy + 3, f"CPU {cpu:.0f}%  ·  {temp:.0f}°C  ·  up {get_uptime_short()}",
                RED if ALERT else DIM, font_small)
    text_right(draw, WIDTH - M, fy + 3, datetime.now().strftime("%a %d %b  %H:%M"), FG, font_small)
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
get_breakdown = _every(STATS_TTL, get_breakdown)

# --- LOOP ---
if __name__ == "__main__":
    while True:
        write_fb(draw_screen(get_stats()))
        time.sleep(UPDATE_INTERVAL)
