#!/usr/bin/env python3
"""Pi-hole LCD (480x320): health board. ALL GOOD / CHECK / PROBLEM banner over six checks (DNS, blocking, CPU, temp, memory, disk)."""
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
font_label  = ImageFont.truetype(FONT_PATH, 10)
font_small  = ImageFont.truetype(FONT_PATH, 12)
font        = ImageFont.truetype(FONT_PATH, 14)
font_value  = ImageFont.truetype(FONT_PATH, 15)
font_clock  = ImageFont.truetype(FONT_PATH, 22)
font_banner = ImageFont.truetype(FONT_PATH, 40)

# --- EXTRA DATA ---
def get_last_seen():
    """Unix time of the newest query in the database (None if unreadable)."""
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT MAX(timestamp) FROM queries;")
        t = c.fetchone()[0]
        conn.close()
        return t
    except Exception as e:
        print(e); return None

def get_disk():
    try: return psutil.disk_usage("/").percent
    except Exception: return 0

# --- DRAW ---
BG    = (8, 10, 14)
PANEL = (24, 28, 36)
FG    = (230, 232, 236)
DIM   = (130, 138, 150)
WHITE = (255, 255, 255)
STATE = {"ok":   ((46, 125, 50),  (102, 187, 106), "ALL GOOD"),
         "warn": ((214, 120, 0),  (255, 183, 77),  "CHECK"),
         "bad":  ((198, 40, 40),  (239, 83, 80),   "PROBLEM")}
RANK = {"ok": 0, "warn": 1, "bad": 2}
FOOTER_H, M, GAP = 20, 10, 8

def grade(v, warn, crit): return "bad" if v >= crit else "warn" if v >= warn else "ok"

def checks(stats):
    cpu, temp, mem, disk = get_cpu(), get_temp(), get_mem(), get_disk()
    out = []
    last = get_last_seen()
    if last is None:
        out.append(("DNS ACTIVITY", "database unreadable", "bad"))
    else:
        age = max(0, time.time() - last)
        msg = "just now" if age < 90 else f"last query {int(age // 60)} min ago"
        out.append(("DNS ACTIVITY", msg, "ok" if age < 300 else "warn" if age < 1800 else "bad"))
    if not stats:
        out.append(("BLOCKING", "no data", "bad"))
    elif stats["total"] and not stats["blocked"]:
        out.append(("BLOCKING", "nothing blocked today", "warn"))
    else:
        out.append(("BLOCKING", f"{stats['blocked_pct']:.1f}% of {stats['total']:,}", "ok"))
    out.append(("CPU",         f"{cpu:.0f}%",    grade(cpu, 60, 80)))
    out.append(("TEMPERATURE", f"{temp:.1f}°C",  grade(temp, 60, 70)))
    out.append(("MEMORY",      f"{mem:.0f}%",    grade(mem, 70, 90)))
    out.append(("DISK",        f"{disk:.0f}%",   grade(disk, 80, 90)))
    return out

def draw_screen(stats):
    results = checks(stats)
    overall = max((s for _, _, s in results), key=RANK.get)
    passing = sum(1 for _, _, s in results if s == "ok")

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # BANNER
    fill, _, word = STATE[overall]
    draw.rectangle((0, 0, WIDTH, 92), fill=fill)
    text(draw, M + 6, 10, word, WHITE, font_banner)
    text(draw, M + 8, 62, f"{get_hostname()}  ·  {passing} of {len(results)} checks passing",
         WHITE, font)
    text_right(draw, WIDTH - M - 6, 14, datetime.now().strftime("%H:%M"), WHITE, font_clock)
    text_right(draw, WIDTH - M - 6, 42, datetime.now().strftime("%a %d %b"), WHITE, font_small)

    # CHECK GRID: 2 columns x 3 rows
    top, bottom = 92 + GAP, HEIGHT - FOOTER_H - GAP
    cw = (WIDTH - 2 * M - GAP) // 2
    ch = (bottom - top - 2 * GAP) // 3
    for i, (label, value, st) in enumerate(results):
        x0 = M + (i % 2) * (cw + GAP)
        y0 = top + (i // 2) * (ch + GAP)
        panel(draw, (x0, y0, x0 + cw, y0 + ch), PANEL)
        dot = STATE[st][1]
        cy = y0 + ch // 2
        draw.ellipse((x0 + 12, cy - 6, x0 + 24, cy + 6), fill=dot)
        text(draw, x0 + 34, y0 + 9, label, DIM, font_label)
        text(draw, x0 + 34, y0 + 22, fit(draw, value, font_value, cw - 44),
             FG if st == "ok" else dot, font_value)

    # FOOTER
    fy = HEIGHT - FOOTER_H
    draw.rectangle((0, fy, WIDTH, HEIGHT), fill=PANEL)
    text(draw, M, fy + 3, f"IP {get_ip()}", DIM, font_small)
    text_right(draw, WIDTH - M, fy + 3, f"up {get_uptime_short()}", DIM, font_small)
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
get_last_seen = _every(30, get_last_seen)

# --- LOOP ---
if __name__ == "__main__":
    while True:
        write_fb(draw_screen(get_stats()))
        time.sleep(UPDATE_INTERVAL)
