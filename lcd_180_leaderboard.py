#!/usr/bin/env python3
"""Pi-hole LCD (480x320): leaderboards. Today's top clients and most-blocked domains, ranked with bars."""
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
font_list  = ImageFont.truetype(FONT_PATH, 13)
font       = ImageFont.truetype(FONT_PATH, 14)
font_title = ImageFont.truetype(FONT_PATH, 16)

# --- TOP LISTS ---
def get_top(n=7):
    """Today's busiest clients and most-blocked domains: ([(name, count)], [(domain, count)])."""
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        today = "timestamp >= strftime('%s','now','start of day')"
        c.execute(f"SELECT client, COUNT(*) FROM queries WHERE {today} GROUP BY client ORDER BY 2 DESC LIMIT ?;", (n,))
        clients = c.fetchall()
        c.execute(f"SELECT domain, COUNT(*) FROM queries WHERE status=1 AND {today} GROUP BY domain ORDER BY 2 DESC LIMIT ?;", (n,))
        domains = c.fetchall()
        # show hostnames where Pi-hole knows them (client_by_id exists in FTL v5.9+)
        names = {}
        try:
            c.execute("SELECT ip, name FROM client_by_id WHERE name IS NOT NULL AND name != '';")
            names = dict(c.fetchall())
        except sqlite3.Error:
            pass
        conn.close()
        return [(names.get(ip, ip), cnt) for ip, cnt in clients], domains
    except Exception as e:
        print(e); return [], []

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

HEADER_H, FOOTER_H, M, GAP = 26, 20, 10, 8

def level(v, warn, crit): return RED if v >= crit else AMBER if v >= warn else GREEN

def ranked(draw, x0, x1, y0, y1, title, rows, color):
    panel(draw, (x0, y0, x1, y1), PANEL)
    text(draw, x0 + 10, y0 + 7, title, DIM, font_label)
    if not rows:
        text(draw, x0 + 10, y0 + 30, "no data", DIM, font_list); return
    peak = rows[0][1] or 1
    y = y0 + 24
    for i, (name, cnt) in enumerate(rows):
        if y > y1 - 26: break
        num = f"{cnt:,}"
        text(draw, x0 + 10, y, f"{i + 1}", DIM, font_list)
        nw = text_w(draw, num, font_list)
        text(draw, x0 + 26, y, fit(draw, str(name), font_list, x1 - x0 - 46 - nw), FG, font_list)
        text_right(draw, x1 - 10, y, num, color, font_list)
        bx0, bx1 = x0 + 26, x1 - 10
        draw.rectangle((bx0, y + 18, bx1, y + 20), fill=TRACK)
        draw.rectangle((bx0, y + 18, bx0 + int((bx1 - bx0) * cnt / peak), y + 20), fill=color)
        y += 32

def draw_screen(stats):
    cpu, temp = get_cpu(), get_temp()
    ALERT = cpu > 80 or temp > 70

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # HEADER
    draw.rectangle((0, 0, WIDTH, HEADER_H), fill=RED if ALERT else PANEL)
    if not ALERT: draw.rectangle((0, HEADER_H - 2, WIDTH, HEADER_H), fill=BLUE)
    text(draw, M, 3, get_hostname(), FG, font_title)
    if stats:
        x = WIDTH - M
        for val, label, col in ((f"{stats['blocked_pct']:.1f}%", " blocked", RED),
                                (f"{stats['total']:,}", " queries   ", FG)):
            text_right(draw, x, 5, label, FG if ALERT else DIM, font); x -= text_w(draw, label, font)
            text_right(draw, x, 5, val, FG if ALERT else col, font);   x -= text_w(draw, val, font)

    # TWO LEADERBOARDS
    top, bottom = HEADER_H + 8, HEIGHT - FOOTER_H - 8
    mid = WIDTH // 2
    clients, domains = get_top()
    ranked(draw, M, mid - GAP // 2, top, bottom, "TOP CLIENTS TODAY", clients, BLUE)
    ranked(draw, mid + GAP // 2, WIDTH - M, top, bottom, "TOP BLOCKED TODAY", domains, RED)

    # FOOTER: IP, system, clock
    fy = HEIGHT - FOOTER_H
    draw.rectangle((0, fy, WIDTH, HEIGHT), fill=PANEL)
    text(draw, M, fy + 3, f"IP {get_ip()}", DIM, font_small)
    sysinfo = f"CPU {cpu:.0f}%   {temp:.0f}°C   up {get_uptime_short()}"
    text_center(draw, WIDTH // 2, fy + 3, sysinfo, RED if ALERT else DIM, font_small)
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
get_top = _every(STATS_TTL, get_top)

# --- LOOP ---
if __name__ == "__main__":
    while True:
        write_fb(draw_screen(get_stats()))
        time.sleep(UPDATE_INTERVAL)
