#!/usr/bin/env python3
"""Pi-hole LCD (480x320): oscilloscope. Queries and blocked per minute over the last hour as two glowing traces on a CRT grid."""
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
MONO_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
font_label = ImageFont.truetype(FONT_PATH, 10)
font_title = ImageFont.truetype(FONT_PATH, 14)
mono       = ImageFont.truetype(MONO_PATH, 11)
mono_big   = ImageFont.truetype(MONO_PATH, 17)

# --- PER-MINUTE DATA ---
MINUTES = 60

def get_trace():
    """Queries and blocked for each of the last MINUTES whole minutes: ([q], [b]), oldest first."""
    end = int(time.time()) // 60 * 60            # start of the current (partial) minute
    start = end - MINUTES * 60
    q, b = [0] * MINUTES, [0] * MINUTES
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("""
        SELECT (timestamp - ?) / 60, COUNT(*), SUM(CASE WHEN status=1 THEN 1 ELSE 0 END)
        FROM queries WHERE timestamp >= ? AND timestamp < ? GROUP BY 1;
        """, (start, start, end))
        for k, n, nb in c.fetchall():
            if 0 <= k < MINUTES: q[int(k)], b[int(k)] = n, nb or 0
        conn.close()
        return q, b
    except Exception as e:
        print(e); return None

# --- DRAW ---
BEZEL = (26, 28, 30)
CRT   = (4, 20, 14)
GRIDC = (22, 62, 44)
GRIDD = (14, 42, 30)
CH1   = (240, 226, 70)          # queries: yellow, like channel 1 on most scopes
CH2   = (70, 210, 245)          # blocked: cyan
FG    = (210, 216, 220)
DIM   = (120, 128, 134)
HOT   = (255, 110, 70)
X0, Y0, X1, Y1 = 10, 10, 370, 266     # screen; 10 x 8 divisions of 36 x 32 px
DX, DY = 10, 8

def per_div(peak):
    """Vertical scale (1-2-5 steps) so the peak fits in 7 of the 8 divisions."""
    for k in range(0, 7):
        for m in (1, 2, 5):
            s = m * 10 ** k
            if peak <= s * (DY - 1): return s
    return 10 ** 7

def trace(draw, values, scale, color):
    pts = []
    for i, v in enumerate(values):
        x = X0 + (X1 - X0) * i / (len(values) - 1)
        y = Y1 - (Y1 - Y0) * min(v / (scale * DY), 1)
        pts.append((x, y))
    glow = tuple(int(c * 0.28 + b * 0.72) for c, b in zip(color, CRT))
    draw.line(pts, fill=glow, width=5)            # cheap phosphor glow
    draw.line(pts, fill=color, width=2)

def knob(draw, x, y, label, value, color):
    text(draw, x, y, label, DIM, font_label)
    text(draw, x, y + 11, value, color, mono_big)

_bg = []

def background():
    """Bezel, screen and graticule: ~1,500 dots, so draw them once and reuse."""
    if _bg: return _bg[0]
    img = Image.new("RGB", (WIDTH, HEIGHT), BEZEL)
    draw = ImageDraw.Draw(img)

    draw.rectangle((X0 - 2, Y0 - 2, X1 + 2, Y1 + 2), fill=CRT)
    gw, gh = (X1 - X0) / DX, (Y1 - Y0) / DY
    for i in range(DX + 1):
        x = X0 + i * gw
        for y in range(Y0, Y1 + 1, 4): draw.point((x, y), fill=GRIDD)
    for j in range(DY + 1):
        y = Y0 + j * gh
        for x in range(X0, X1 + 1, 4): draw.point((x, y), fill=GRIDD)
    cx, cy = (X0 + X1) / 2, (Y0 + Y1) / 2
    draw.line((cx, Y0, cx, Y1), fill=GRIDC)
    draw.line((X0, cy, X1, cy), fill=GRIDC)
    for i in range(DX * 5 + 1):                   # minor ticks on the centre lines
        x = X0 + i * gw / 5
        draw.line((x, cy - 2, x, cy + 2), fill=GRIDC)
    for j in range(DY * 5 + 1):
        y = Y0 + j * gh / 5
        draw.line((cx - 2, y, cx + 2, y), fill=GRIDC)
    _bg.append(img)
    return img

def draw_screen(stats):
    cpu, temp = get_cpu(), get_temp()
    ALERT = cpu > 80 or temp > 70
    data = get_trace()
    q, b = data if data else ([0] * MINUTES, [0] * MINUTES)
    scale = per_div(max(q) or 1)

    img = background().copy()          # graticule is drawn once, then reused
    draw = ImageDraw.Draw(img)

    trace(draw, b, scale, CH2)
    trace(draw, q, scale, CH1)
    text(draw, X0 + 6, Y0 + 4, "CH1 QUERIES", CH1, font_label)
    text(draw, X0 + 16 + text_w(draw, "CH1 QUERIES", font_label), Y0 + 4, "CH2 BLOCKED", CH2, font_label)
    text_right(draw, X1 - 6, Y0 + 4, "-60 min  →  now", GRIDC, font_label)

    # side controls
    sx = X1 + 14
    text(draw, sx, 10, get_hostname(), FG, font_title)
    text(draw, sx, 28, datetime.now().strftime("%H:%M"), DIM, font_label)
    knob(draw, sx, 48, "CH1 /MIN", f"{q[-1]}", CH1)
    knob(draw, sx, 84, "CH2 /MIN", f"{b[-1]}", CH2)
    knob(draw, sx, 120, "VOLTS/DIV", f"{scale}", FG)
    knob(draw, sx, 156, "TIME/DIV", "6 min", FG)
    knob(draw, sx, 192, "TRIGGER", "AUTO", FG)
    draw.ellipse((sx, 236, sx + 10, 246), fill=HOT if ALERT else (60, 200, 90))
    text(draw, sx + 16, 234, "HOT" if ALERT else "READY", HOT if ALERT else DIM, font_label)

    # measurement bar
    qs = [v for v in q if v] or [0]
    avg = sum(q) / MINUTES
    ratio = sum(b) / sum(q) * 100 if sum(q) else 0
    meas = f"CH1 avg {avg:.1f}  max {max(qs)}   CH2 {ratio:.1f}% of CH1"
    text(draw, X0, 276, meas, FG, mono)
    left = (f"today {stats['total']:,} q · {stats['blocked_pct']:.1f}% blocked" if stats else "no data")
    text(draw, X0, 296, left, DIM, mono)
    text_right(draw, WIDTH - 10, 296, f"{temp:.0f}°C CPU {cpu:.0f}%", HOT if ALERT else DIM, mono)
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
get_trace = _every(60, get_trace)

# --- LOOP ---
if __name__ == "__main__":
    while True:
        write_fb(draw_screen(get_stats()))
        time.sleep(UPDATE_INTERVAL)
