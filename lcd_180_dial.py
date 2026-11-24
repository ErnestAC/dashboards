#!/usr/bin/env python3
"""Pi-hole LCD (480x320): speedometer of queries per minute (last 5 min), with the blocked share on an inner arc."""
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

import math

# --- FONTS ---
FONT_PATH = "/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf"
font_label = ImageFont.truetype(FONT_PATH, 10)
font_small = ImageFont.truetype(FONT_PATH, 12)
font       = ImageFont.truetype(FONT_PATH, 14)
font_title = ImageFont.truetype(FONT_PATH, 16)
font_stat  = ImageFont.truetype(FONT_PATH, 18)
font_value = ImageFont.truetype(FONT_PATH, 46)

# --- QUERY RATE ---
def get_rate():
    """(queries, blocked) per minute, averaged over the last 5 minutes."""
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("""
        SELECT COUNT(*), SUM(CASE WHEN status=1 THEN 1 ELSE 0 END)
        FROM queries WHERE timestamp >= strftime('%s','now','-5 minutes');
        """)
        n, b = c.fetchone()
        conn.close()
        return (n or 0) / 5, (b or 0) / 5
    except Exception as e:
        print(e); return None

_scale = {"max": 20}

def dial_max(rate):
    """Round full-scale value (20, 50, 100, 200, ...) that only ever grows,
    so the needle position keeps the same meaning from frame to frame."""
    steps = [m * 10 ** k for k in range(1, 7) for m in (1, 2, 5)]
    while rate > _scale["max"] * 0.9:
        _scale["max"] = next(s for s in steps if s > _scale["max"])
    return _scale["max"]

# --- DRAW ---
BG    = (8, 10, 14)
PANEL = (24, 28, 36)
TRACK = (40, 46, 58)
FG    = (230, 232, 236)
DIM   = (130, 138, 150)
RED   = (239, 83, 80)
BLUE  = (66, 165, 245)
GREEN = (102, 187, 106)
AMBER = (255, 183, 77)

HEADER_H, M = 26, 10

def level(v, warn, crit): return RED if v >= crit else AMBER if v >= warn else GREEN

def polar(cx, cy, r, deg):
    a = math.radians(deg)
    return cx + r * math.cos(a), cy + r * math.sin(a)

def fmt_rate(r): return f"{r:.0f}" if r >= 10 else f"{r:.1f}"

def draw_screen(stats):
    cpu, temp = get_cpu(), get_temp()
    ALERT = cpu > 80 or temp > 70
    rate = get_rate()
    qpm, bpm = rate if rate else (0, 0)
    top = dial_max(qpm)

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # HEADER
    draw.rectangle((0, 0, WIDTH, HEADER_H), fill=RED if ALERT else PANEL)
    text(draw, M, 3, get_hostname(), FG, font_title)
    text_right(draw, WIDTH - M, 5, datetime.now().strftime("%a %d %b  %H:%M"), FG if ALERT else DIM, font)

    # DIAL (top half-circle; PIL angles: 180 = left, 270 = up, 360 = right)
    cx, cy, R = WIDTH // 2, 196, 140
    box = (cx - R, cy - R, cx + R, cy + R)
    frac = min(1.0, qpm / top)
    bfrac = min(1.0, bpm / top)
    draw.arc(box, 180, 360, fill=TRACK, width=16)
    if frac > 0:
        draw.arc(box, 180, 180 + 180 * frac, fill=BLUE, width=16)
    ib = (cx - R + 24, cy - R + 24, cx + R - 24, cy + R - 24)
    draw.arc(ib, 180, 360, fill=PANEL, width=6)
    if bfrac > 0:
        draw.arc(ib, 180, 180 + 180 * bfrac, fill=RED, width=6)
    for i in range(11):                                  # ticks
        deg = 180 + 18 * i
        r0 = R + 4
        r1 = R + (13 if i % 5 == 0 else 8)
        draw.line((*polar(cx, cy, r0, deg), *polar(cx, cy, r1, deg)), fill=DIM, width=2)
    text_right(draw, cx - R - 4, cy + 4, "0", DIM, font_small)
    text_center(draw, cx, cy - R - 32, f"{top // 2:,}", DIM, font_small)
    text(draw, cx + R + 4, cy + 4, f"{top:,}", DIM, font_small)

    # needle + hub
    tip = polar(cx, cy, R - 34, 180 + 180 * frac)
    draw.line((cx, cy, *tip), fill=FG, width=4)
    draw.ellipse((cx - 9, cy - 9, cx + 9, cy + 9), fill=FG)

    # reading under the hub
    text_center(draw, cx, cy + 14, fmt_rate(qpm), FG, font_value)
    text_center(draw, cx, cy + 66, "QUERIES / MIN  ·  LAST 5 MIN", DIM, font_label)

    # corner stats under the dial's ends
    y0 = cy + 30
    text(draw, M, y0, "BLOCKED / MIN", DIM, font_label)
    text(draw, M, y0 + 12, fmt_rate(bpm), RED, font_stat)
    text(draw, M, y0 + 40, "BLOCKED TODAY", DIM, font_label)
    text(draw, M, y0 + 52, f"{stats['blocked_pct']:.1f}%" if stats else "–", RED, font_stat)
    text_right(draw, WIDTH - M, y0, "CPU · TEMP", DIM, font_label)
    text_right(draw, WIDTH - M, y0 + 12, f"{cpu:.0f}% · {temp:.0f}°C",
               RED if ALERT else level(max(cpu, temp), 60, 75), font_stat)
    text_right(draw, WIDTH - M, y0 + 40, "CLIENTS TODAY", DIM, font_label)
    text_right(draw, WIDTH - M, y0 + 52, f"{stats['clients']}" if stats else "–", FG, font_stat)
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
get_rate = _every(30, get_rate)

# --- LOOP ---
if __name__ == "__main__":
    while True:
        write_fb(draw_screen(get_stats()))
        time.sleep(UPDATE_INTERVAL)
