#!/usr/bin/env python3
"""Pi-hole LCD (480x320): 4-shade handheld. Chunky green pixel art at 240x160, doubled: a character sheet for the Pi with bars, stats and a dialog box for the latest blocked domain."""
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
    "!": "00100 00100 00100 00100 00100 00000 00100", "°": "01100 10010 10010 01100 00000 00000 00000",
}

# The Pi's portrait: a 16x16 shield, 0-3 = palette index (0 = transparent)
SHIELD = """
................
..333333333333..
.32222222222223.
.32111111111123.
.32111133111123.
.32111333311123.
.32113333331123.
.32111133111123.
.32111133111123.
.32111133111123.
..321111111123..
..322111111223..
...3221111223...
....33222233....
......3333......
................
""".split()

# --- DRAW ---
LW, LH = WIDTH // 2, HEIGHT // 2          # drawn at 240x160, shown doubled
PAL = [(155, 188, 15), (139, 172, 15), (48, 98, 48), (15, 56, 15)]   # lightest .. darkest
L0, L1, D2, D3 = PAL

def gtext(draw, x, y, s, c, scale=1):
    for ch in s.upper():
        rows = GLYPHS.get(ch, GLYPHS[" "]).split()
        for gy, row in enumerate(rows):
            for gx, bit in enumerate(row):
                if bit == "1":
                    draw.rectangle((x + gx * scale, y + gy * scale,
                                    x + gx * scale + scale - 1, y + gy * scale + scale - 1), fill=c)
        x += 6 * scale
    return x

def gw(s, scale=1): return len(s) * 6 * scale - scale

def box(draw, x0, y0, x1, y1):
    """Window with a double border, like a menu frame."""
    draw.rectangle((x0, y0, x1, y1), fill=L0, outline=D3)
    draw.rectangle((x0 + 2, y0 + 2, x1 - 2, y1 - 2), outline=D2)

def bar(draw, x, y, w, label, pct, warn):
    """Labelled segmented bar; the fill drops to the dark shade when warn."""
    gtext(draw, x, y, label, D3)
    bx = x + 24
    draw.rectangle((bx, y, bx + w, y + 6), outline=D3, fill=L1)
    n = int((w - 2) / 4)
    lit = round(n * max(0, min(100, pct)) / 100)
    for k in range(lit):
        draw.rectangle((bx + 2 + k * 4, y + 2, bx + 4 + k * 4, y + 4), fill=D3 if warn else D2)
    gtext(draw, bx + w + 4, y, f"{pct:.0f}%", D3)

def draw_screen(stats):
    cpu, temp, mem = get_cpu(), get_temp(), get_mem()
    now = datetime.now()
    img = Image.new("RGB", (LW, LH), L1)
    draw = ImageDraw.Draw(img)

    # header window: portrait, name, level (= days up)
    box(draw, 2, 2, LW - 3, 50)
    for y, row in enumerate(SHIELD):
        for x, ch in enumerate(row):
            if ch != ".": draw.point((10 + x, 10 + y), fill=PAL[int(ch)])
    name = get_hostname()[:9]
    gtext(draw, 32, 9, name, D3, 2)
    up = get_uptime_short()
    lv = up.split("d")[0] if "d" in up else "0"
    gtext(draw, 32, 28, f"LV {lv}", D2)
    gtext(draw, 32 + gw(f"LV {lv}") + 10, 28, "PI-HOLE", D2)
    gtext(draw, LW - 10 - gw(now.strftime("%H:%M")), 9, now.strftime("%H:%M"), D3)
    draw.line((32, 39, LW - 10, 39), fill=D2)
    gtext(draw, 32, 41, f"UP {up}", D3)

    # stats window
    box(draw, 2, 52, LW - 3, 110)
    bar(draw, 8, 58, 80, "CPU", cpu, cpu > 80)
    bar(draw, 8, 68, 80, "MEM", mem, mem > 90)
    bar(draw, 8, 78, 80, "HOT", min(100, temp / 85 * 100), temp > 70)
    if stats:
        rows = [("QRY", f"{stats['total']}"), ("BLK", f"{stats['blocked']}"),
                ("PCT", f"{stats['blocked_pct']:.1f}%"), ("CLI", f"{stats['clients']}")]
    else:
        rows = [("QRY", "---"), ("BLK", "---"), ("PCT", "---"), ("CLI", "---")]
    sx = 150
    draw.line((sx - 6, 56, sx - 6, 106), fill=D2)
    for i, (k, v) in enumerate(rows):
        gtext(draw, sx, 58 + i * 12, k, D2)
        gtext(draw, LW - 9 - gw(v), 58 + i * 12, v, D3)
    gtext(draw, 8, 94, "TEMP", D2)
    gtext(draw, 8 + 30, 94, f"{temp:.0f}°C", D3)
    gtext(draw, 80, 94, now.strftime("%a %d").upper(), D2)

    # dialog box: latest blocked domain, a different one every 8 s
    box(draw, 2, 112, LW - 3, LH - 3)
    if stats and stats["last_blocked"]:
        dom = stats["last_blocked"][int(time.time() // 8) % len(stats["last_blocked"])].upper()
        dom = dom if len(dom) <= 36 else dom[:34] + ".."
        gtext(draw, 9, 119, f"{name.upper()} BLOCKED", D3)
        gtext(draw, 9, 131, dom + "!", D3)
    else:
        gtext(draw, 9, 119, "NOTHING BLOCKED YET.", D3)
    # the "more" triangle
    tx, ty = LW - 14, LH - 13
    draw.polygon([(tx, ty), (tx + 6, ty), (tx + 3, ty + 3)], fill=D3)

    return img.resize((WIDTH, HEIGHT), Image.NEAREST)

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
