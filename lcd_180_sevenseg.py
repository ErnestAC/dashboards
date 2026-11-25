#!/usr/bin/env python3
"""Pi-hole LCD (480x320): seven-segment LEDs. Big red clock and LED readouts for blocked %, queries and temperature, unlit segments ghosted."""
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
font_label = ImageFont.truetype(FONT_PATH, 10)
font_small = ImageFont.truetype(FONT_PATH, 12)

# --- SEVEN-SEGMENT DRAWING ---
# Digits are polygons, not a font, so they look like real LEDs at any size.
SEGMENTS = {
    "0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc",
    "5": "afgcd", "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcdfg",
    "-": "g", " ": "",
}
SLANT = 0.08                     # italic lean, like most LED displays

def seg_polys(x, y, w, h, t):
    """The seven segment polygons of one digit cell, keyed a..g."""
    g = max(1, t // 5)
    xl, xr = x + t / 2, x + w - t / 2
    yt, ym, yb = y + t / 2, y + h / 2, y + h - t / 2
    def H(x0, x1, yc):
        return [(x0, yc), (x0 + t / 2, yc - t / 2), (x1 - t / 2, yc - t / 2),
                (x1, yc), (x1 - t / 2, yc + t / 2), (x0 + t / 2, yc + t / 2)]
    def V(xc, y0, y1):
        return [(xc, y0), (xc + t / 2, y0 + t / 2), (xc + t / 2, y1 - t / 2),
                (xc, y1), (xc - t / 2, y1 - t / 2), (xc - t / 2, y0 + t / 2)]
    polys = {
        "a": H(xl + g, xr - g, yt), "g": H(xl + g, xr - g, ym), "d": H(xl + g, xr - g, yb),
        "f": V(xl, yt + g, ym - g), "b": V(xr, yt + g, ym - g),
        "e": V(xl, ym + g, yb - g), "c": V(xr, ym + g, yb - g),
    }
    lean = lambda p: (p[0] + (y + h - p[1]) * SLANT, p[1])
    return {k: [lean(p) for p in v] for k, v in polys.items()}

_polys = {}                      # geometry cache: same cells every frame

def digit(draw, x, y, w, h, t, ch, on, off):
    key = (x, y, w, h, t)
    if key not in _polys: _polys[key] = seg_polys(x, y, w, h, t)
    lit = SEGMENTS.get(ch, "")
    for k, poly in _polys[key].items():
        draw.polygon(poly, fill=on if k in lit else off)

def dot(draw, x, y, r, color):
    draw.ellipse((x - r, y - r, x + r, y + r), fill=color)

def readout(draw, x_right, y, s, cells, w, h, t, gap, on, off):
    """Right-aligned LED number in `cells` digit cells; '.' lights a decimal point."""
    chars, points = [], set()
    for ch in s:
        if ch == "." and chars: points.add(len(chars) - 1)
        else: chars.append(ch)
    chars = [" "] * (cells - len(chars)) + chars[-cells:]
    shift = cells - len(s.replace(".", ""))
    points = {p + shift for p in points}
    x = x_right - cells * (w + gap) + gap
    for i, ch in enumerate(chars):
        digit(draw, x, y, w, h, t, ch, on, off)
        dot(draw, x + w + gap / 2, y + h - t / 2, t * 0.55, on if i in points else off)
        x += w + gap

# --- DRAW ---
BG    = (6, 6, 6)
PANEL = (17, 17, 18)
LABEL = (120, 120, 120)
RED   = (255, 42, 30)
AMBER = (255, 150, 20)
GREEN = (60, 255, 100)
CYAN  = (40, 200, 255)

def ghost(c, k=0.09):
    return tuple(int(v * k + b * (1 - k)) for v, b in zip(c, PANEL))

def lit_panel(draw, box, label, sub=""):
    panel(draw, box, PANEL)
    text(draw, box[0] + 10, box[1] + 6, label, LABEL, font_label)
    if sub: text_right(draw, box[2] - 10, box[1] + 6, sub, LABEL, font_label)

def draw_screen(stats):
    cpu, temp, mem = get_cpu(), get_temp(), get_mem()
    ALERT = cpu > 80 or temp > 70
    now = datetime.now()

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # clock: HH:MM (steady colon, so unchanged frames can be skipped)
    panel(draw, (8, 8, WIDTH - 8, 166), PANEL)
    W, Hh, T, GAP = 70, 124, 13, 16
    x0 = (WIDTH - (4 * W + 2 * GAP + 44)) // 2
    hh, mm = now.strftime("%H"), now.strftime("%M")
    off = ghost(RED)
    for i, ch in enumerate(hh):
        digit(draw, x0 + i * (W + GAP), 22, W, Hh, T, ch, RED, off)
    cx = x0 + 2 * W + GAP + 22
    dot(draw, cx + 3, 22 + Hh * 0.32, 7, RED)
    dot(draw, cx - 1, 22 + Hh * 0.72, 7, RED)
    for i, ch in enumerate(mm):
        digit(draw, cx + 22 + i * (W + GAP), 22, W, Hh, T, ch, RED, off)
    text(draw, 20, 148, now.strftime("%a %d %b").upper(), LABEL, font_label)
    text_right(draw, WIDTH - 20, 148, get_hostname(), LABEL, font_label)

    # readouts
    w, h, t, gap = 27, 60, 7, 9
    y0, y1 = 174, 286
    dy = y0 + 32
    lit_panel(draw, (8, y0, 140, y1), "BLOCKED", "%")
    pct = f"{stats['blocked_pct']:.1f}" if stats else "---"
    readout(draw, 140 - 12, dy, pct, 3, w, h, t, gap, AMBER, ghost(AMBER))

    total = stats["total"] if stats else None
    big = total is not None and total > 99999
    lit_panel(draw, (148, y0, 352, y1), "QUERIES TODAY", "×1000" if big else "")
    q = "-----" if total is None else f"{total / 1000:.1f}" if big else f"{total}"
    readout(draw, 352 - 12, dy, q, 5, w, h, t, gap, GREEN, ghost(GREEN))

    tc = RED if temp > 70 else CYAN
    lit_panel(draw, (360, y0, WIDTH - 8, y1), "TEMP", "°C")
    readout(draw, WIDTH - 8 - 12, dy, f"{min(99, round(temp))}", 2, w, h, t, gap, tc, ghost(tc))

    line = f"CPU {cpu:.0f}%   ·   MEM {mem:.0f}%   ·   UP {get_uptime_short()}"
    if stats: line += f"   ·   {stats['clients']} CLIENTS"
    text_center(draw, WIDTH // 2, 296, line, RED if ALERT else LABEL, font_small)
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
