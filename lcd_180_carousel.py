#!/usr/bin/env python3
"""Pi-hole LCD (480x320): rotates between overview, latest blocked and system pages every 12 seconds."""
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
font_label = ImageFont.truetype(FONT_PATH, 11)
font_small = ImageFont.truetype(FONT_PATH, 13)
font_list  = ImageFont.truetype(FONT_PATH, 16)
font       = ImageFont.truetype(FONT_PATH, 14)
font_title = ImageFont.truetype(FONT_PATH, 16)
font_stat  = ImageFont.truetype(FONT_PATH, 26)
font_huge  = ImageFont.truetype(FONT_PATH, 92)

# --- DRAW ---
PAGE_SECONDS = 12          # how long each page stays up
BG    = (8, 10, 14)
PANEL = (24, 28, 36)
TRACK = (48, 54, 66)
FG    = (230, 232, 236)
DIM   = (130, 138, 150)
RED   = (239, 83, 80)
BLUE  = (66, 165, 245)
GREEN = (102, 187, 106)
AMBER = (255, 183, 77)
HEADER_H, M = 30, 16

def level(v, warn, crit): return RED if v >= crit else AMBER if v >= warn else GREEN

def page_overview(draw, stats, top):
    draw.text((WIDTH // 2, top + 112), f"{stats['blocked_pct']:.1f}%", fill=RED, font=font_huge, anchor="ms")
    text_center(draw, WIDTH // 2, top + 122, "OF TODAY'S QUERIES BLOCKED", DIM, font_label)
    cols = [("QUERIES", f"{stats['total']:,}", FG), ("BLOCKED", f"{stats['blocked']:,}", RED),
            ("CLIENTS", f"{stats['clients']}", FG)]
    for i, (label, val, col) in enumerate(cols):
        cx = WIDTH * (2 * i + 1) // 6
        text_center(draw, cx, top + 158, label, DIM, font_label)
        text_center(draw, cx, top + 174, val, col, font_stat)

def page_blocked(draw, stats, top):
    text(draw, M, top + 14, "LATEST BLOCKED", DIM, font_label)
    y = top + 36
    for d in stats["last_blocked"][:7]:
        draw.ellipse((M, y + 6, M + 7, y + 13), fill=RED)
        text(draw, M + 18, y, fit(draw, d, font_list, WIDTH - 2 * M - 18), FG, font_list)
        y += 28

def page_system(draw, stats, top):
    cpu, temp, mem = get_cpu(), get_temp(), get_mem()
    y = top + 24
    for label, val, pct, col in (("CPU", f"{cpu:.0f}%", cpu, level(cpu, 60, 80)),
                                 ("MEMORY", f"{mem:.0f}%", mem, level(mem, 70, 90)),
                                 ("TEMPERATURE", f"{temp:.1f}°C", temp / 85 * 100, level(temp, 60, 70))):
        text(draw, M, y, label, DIM, font_label)
        text_right(draw, WIDTH - M, y - 6, val, col, font_stat)
        draw.rectangle((M, y + 26, WIDTH - M, y + 32), fill=TRACK)
        draw.rectangle((M, y + 26, M + int((WIDTH - 2 * M) * max(0, min(100, pct)) / 100), y + 32), fill=col)
        y += 54
    text(draw, M, y + 4, f"up {get_uptime_short()}   ·   IP {get_ip()}", DIM, font_small)

PAGES = [("Overview", page_overview), ("Blocked", page_blocked), ("System", page_system)]

def draw_screen(stats):
    cpu, temp = get_cpu(), get_temp()
    ALERT = cpu > 80 or temp > 70
    idx = int(time.time() // PAGE_SECONDS) % len(PAGES)
    if not stats and PAGES[idx][0] != "System":
        idx = 2                                     # only the system page works without data

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # HEADER: host, page name, clock
    draw.rectangle((0, 0, WIDTH, HEADER_H), fill=RED if ALERT else PANEL)
    text(draw, M, 6, get_hostname(), FG, font_title)
    text_center(draw, WIDTH // 2, 8, PAGES[idx][0].upper(), FG if ALERT else DIM, font)
    text_right(draw, WIDTH - M, 6, datetime.now().strftime("%H:%M"), FG, font_title)

    PAGES[idx][1](draw, stats, HEADER_H)

    # page dots
    for i in range(len(PAGES)):
        x = WIDTH // 2 + (i - 1) * 16
        col = FG if i == idx else TRACK
        draw.ellipse((x - 4, HEIGHT - 16, x + 4, HEIGHT - 8), fill=col)
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
