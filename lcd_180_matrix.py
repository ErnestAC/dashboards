#!/usr/bin/env python3
"""Pi-hole LCD (480x320): LED dot-matrix board. Every pixel is a round amber LED: big clock and blocked %, counters, a 24-hour bar graph and the latest blocked domain."""
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

# --- HOURLY ---
def get_hourly():
    """Last 24 hours, oldest first: [(total, blocked)] per hour."""
    try:
        now = int(time.time())
        start = now - now % 3600 - 23 * 3600
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("""
        SELECT (timestamp - ?) / 3600, COUNT(*), SUM(CASE WHEN status=1 THEN 1 ELSE 0 END)
        FROM queries WHERE timestamp >= ? GROUP BY 1;
        """, (start, start))
        hours = [(0, 0)] * 24
        for h, n, b in c.fetchall():
            if 0 <= h < 24: hours[h] = (n, b or 0)
        conn.close()
        return hours
    except Exception as e:
        print(e); return [(0, 0)] * 24

# --- 5x7 FONT ---
# Each glyph is 7 rows of 5 bits, top row first.
GLYPHS = {
    "0": "01110 10001 10011 10101 11001 10001 01110", "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111", "3": "11111 00010 00100 00010 00001 10001 01110",
    "4": "00010 00110 01010 10010 11111 00010 00010", "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110", "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110", "9": "01110 10001 10001 01111 00001 00010 01100",
    "A": "01110 10001 10001 11111 10001 10001 10001", "B": "11110 10001 10001 11110 10001 10001 11110",
    "C": "01110 10001 10000 10000 10000 10001 01110", "D": "11100 10010 10001 10001 10001 10010 11100",
    "E": "11111 10000 10000 11110 10000 10000 11111", "F": "11111 10000 10000 11110 10000 10000 10000",
    "G": "01110 10001 10000 10111 10001 10001 01111", "H": "10001 10001 10001 11111 10001 10001 10001",
    "I": "01110 00100 00100 00100 00100 00100 01110", "J": "00111 00010 00010 00010 00010 10010 01100",
    "K": "10001 10010 10100 11000 10100 10010 10001", "L": "10000 10000 10000 10000 10000 10000 11111",
    "M": "10001 11011 10101 10101 10001 10001 10001", "N": "10001 10001 11001 10101 10011 10001 10001",
    "O": "01110 10001 10001 10001 10001 10001 01110", "P": "11110 10001 10001 11110 10000 10000 10000",
    "Q": "01110 10001 10001 10001 10101 10010 01101", "R": "11110 10001 10001 11110 10100 10010 10001",
    "S": "01111 10000 10000 01110 00001 00001 11110", "T": "11111 00100 00100 00100 00100 00100 00100",
    "U": "10001 10001 10001 10001 10001 10001 01110", "V": "10001 10001 10001 10001 10001 01010 00100",
    "W": "10001 10001 10001 10101 10101 10101 01010", "X": "10001 10001 01010 00100 01010 10001 10001",
    "Y": "10001 10001 10001 01010 00100 00100 00100", "Z": "11111 00001 00010 00100 01000 10000 11111",
    ":": "00000 01100 01100 00000 01100 01100 00000", ".": "00000 00000 00000 00000 00000 01100 01100",
    "%": "11000 11001 00010 00100 01000 10011 00011", "-": "00000 00000 00000 11111 00000 00000 00000",
    "/": "00000 00001 00010 00100 01000 10000 00000", " ": "00000 00000 00000 00000 00000 00000 00000",
    "°": "01100 10010 10010 01100 00000 00000 00000", ">": "01000 00100 00010 00001 00010 00100 01000",
}

# --- DOT GRID ---
PITCH = 4                          # pixels per LED
COLS, ROWS = WIDTH // PITCH, HEIGHT // PITCH   # 120 x 80 LEDs

BG    = (8, 5, 3)
OFF   = (34, 20, 10)               # unlit LED
AMBER = (255, 160, 30)
RED   = (255, 50, 30)
GREEN = (90, 255, 90)

def put(grid, x, y, color):
    if 0 <= x < COLS and 0 <= y < ROWS: grid[y][x] = color

def glyph_text(grid, x, y, s, color, scale=1):
    """Write s at LED (x, y); each font pixel becomes scale x scale LEDs. Returns end x."""
    for ch in s.upper():
        rows = GLYPHS.get(ch, GLYPHS[" "]).split()
        for gy, row in enumerate(rows):
            for gx, bit in enumerate(row):
                if bit == "1":
                    for dy in range(scale):
                        for dx in range(scale):
                            put(grid, x + gx * scale + dx, y + gy * scale + dy, color)
        x += 6 * scale
    return x

def glyph_w(s, scale=1): return len(s) * 6 * scale - scale

def line_lr(grid, y, left, right, color):
    """A 20-character line: label on the left, value flush right."""
    glyph_text(grid, 0, y, left, AMBER)
    glyph_text(grid, COLS - glyph_w(right) - 1, y, right, color)

def draw_screen(stats):
    cpu, temp = get_cpu(), get_temp()
    now = datetime.now()
    grid = [[None] * COLS for _ in range(ROWS)]

    # big clock and blocked %
    glyph_text(grid, 1, 1, now.strftime("%H:%M"), AMBER, 2)
    pct = f"{min(stats['blocked_pct'], 99.9):.1f}" if stats else "--.-"
    x = COLS - glyph_w(pct, 2) - 8                 # small % sign after the big number
    glyph_text(grid, x, 1, pct, RED, 2)
    glyph_text(grid, COLS - 6, 8, "%", RED)

    for x in range(0, COLS, 2): put(grid, x, 17, AMBER)

    if stats:
        line_lr(grid, 20, "QUERIES", f"{stats['total']}", GREEN)
        line_lr(grid, 29, "BLOCKED", f"{stats['blocked']}", RED)
    line_lr(grid, 38, "TEMP/CPU", f"{temp:.0f}° {cpu:.0f}%", RED if temp > 70 or cpu > 80 else GREEN)

    # 24 hours: 4-LED bars, blocked share on top in red
    hours = get_hourly()
    top = max((n for n, _ in hours), default=0) or 1
    base, hmax = 66, 18
    for i, (n, b) in enumerate(hours):
        h = round(n / top * hmax) if n else 0
        hb = round(b / top * hmax) if b else 0
        for k in range(h):
            for dx in range(4):
                put(grid, i * 5 + dx, base - k, RED if k >= h - hb else GREEN)
    for x in range(0, COLS, 2): put(grid, x, base + 1, AMBER)

    # latest blocked domain, a different one every 8 s
    if stats and stats["last_blocked"]:
        dom = stats["last_blocked"][int(time.time() // 8) % len(stats["last_blocked"])]
        dom = dom.upper() if len(dom) <= 19 else dom.upper()[:17] + ".."
        glyph_text(grid, 0, 72, ">" + dom, RED)

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    r = PITCH * 0.42
    for y in range(ROWS):
        cy = y * PITCH + PITCH / 2
        for x in range(COLS):
            cx = x * PITCH + PITCH / 2
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=grid[y][x] or OFF)
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
get_hourly = _every(STATS_TTL, get_hourly)

# --- LOOP ---
if __name__ == "__main__":
    while True:
        write_fb(draw_screen(get_stats()))
        time.sleep(UPDATE_INTERVAL)
