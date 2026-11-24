#!/usr/bin/env python3
"""Pi-hole LCD (480x320): split screen. Blocked vs allowed today as two huge numbers, system line below."""
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

# --- FONTS ---
FONT_PATH = "/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf"
font_label = ImageFont.truetype(FONT_PATH, 12)
font_small = ImageFont.truetype(FONT_PATH, 13)
font       = ImageFont.truetype(FONT_PATH, 14)
font_sub   = ImageFont.truetype(FONT_PATH, 16)
# big-number sizes, loaded once so nothing is reloaded per frame
BIG_FONTS = [ImageFont.truetype(FONT_PATH, s) for s in (84, 72, 62, 54, 46, 38)]

# --- DRAW ---
BG      = (8, 10, 14)
PANEL   = (24, 28, 36)
FG      = (236, 238, 242)
DIM     = (150, 156, 168)
RED_BG  = (52, 16, 20)
RED     = (239, 83, 80)
BLUE_BG = (12, 30, 52)
BLUE    = (66, 165, 245)
ALERT_BG = (150, 30, 30)
STRIP_H, M = 26, 10

def big_font(draw, s, maxw):
    """Largest preloaded font in which s fits maxw pixels."""
    for f in BIG_FONTS:
        if text_w(draw, s, f) <= maxw:
            return f
    return BIG_FONTS[-1]

def half(draw, x0, x1, label, number, pct_line, extra, accent, bg):
    draw.rectangle((x0, STRIP_H, x1, HEIGHT - STRIP_H), fill=bg)
    cx = (x0 + x1) // 2
    text_center(draw, cx, STRIP_H + 18, label, accent, font_label)
    f = big_font(draw, number, x1 - x0 - 28)
    draw.text((cx, 168), number, fill=FG, font=f, anchor="ms")
    text_center(draw, cx, 182, pct_line, accent, font_sub)
    text_center(draw, cx, 216, fit(draw, extra, font_small, x1 - x0 - 24), DIM, font_small)

def draw_screen(stats):
    cpu, temp, mem = get_cpu(), get_temp(), get_mem()
    ALERT = cpu > 80 or temp > 70

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # TOP STRIP
    draw.rectangle((0, 0, WIDTH, STRIP_H), fill=PANEL)
    text(draw, M, 5, get_hostname(), FG, font)
    if stats:
        text_center(draw, WIDTH // 2, 5, f"{stats['total']:,} queries today", DIM, font)
    text_right(draw, WIDTH - M, 5, datetime.now().strftime("%H:%M"), FG, font)

    mid = WIDTH // 2
    if stats:
        allowed = stats["total"] - stats["blocked"]
        latest = stats["last_blocked"][0] if stats["last_blocked"] else "–"
        half(draw, 0, mid - 1, "BLOCKED", f"{stats['blocked']:,}",
             f"{stats['blocked_pct']:.1f}% of queries", f"latest: {latest}", RED, RED_BG)
        half(draw, mid + 1, WIDTH, "ALLOWED", f"{allowed:,}",
             f"{100 - stats['blocked_pct']:.1f}% of queries",
             f"{stats['clients']} devices · last query {stats['last']}", BLUE, BLUE_BG)
    else:
        text_center(draw, mid, 150, "Pi-hole database unavailable", DIM, font_sub)

    # BOTTOM STRIP: system
    fy = HEIGHT - STRIP_H
    draw.rectangle((0, fy, WIDTH, HEIGHT), fill=ALERT_BG if ALERT else PANEL)
    sysline = f"CPU {cpu:.0f}%    MEM {mem:.0f}%    {temp:.0f}°C    up {get_uptime_short()}    {get_ip()}"
    text_center(draw, WIDTH // 2, fy + 6, sysline, FG if ALERT else DIM, font_small)
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
