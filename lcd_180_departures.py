#!/usr/bin/env python3
"""Pi-hole LCD (480x320): airport departures board. Latest blocked domains as split-flap rows, every flight DENIED."""
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
font       = ImageFont.truetype(FONT_PATH, 13)
font_title = ImageFont.truetype(FONT_PATH, 20)
flap_font  = ImageFont.truetype(MONO_PATH, 15)

# --- DEPARTURES DATA ---
BOARD_ROWS = 9

def get_departures():
    """Newest blocked queries, one row per domain: [(timestamp, client, domain)]."""
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT timestamp, client, domain FROM queries WHERE status=1 "
                  "ORDER BY timestamp DESC LIMIT 60;")
        rows = c.fetchall()
        names = {}
        try:
            c.execute("SELECT ip, name FROM client_by_id WHERE name IS NOT NULL AND name != '';")
            names = dict(c.fetchall())
        except sqlite3.Error:
            pass
        conn.close()
        out, seen = [], set()
        for ts, cl, d in rows:
            if d in seen: continue
            seen.add(d); out.append((ts, cl, names.get(cl, ""), d))
            if len(out) == BOARD_ROWS: break
        return out
    except Exception as e:
        print(e); return []

def flight_code(ip, name):
    """'MA21' style code: two letters of the device name + last part of its address."""
    letters = "".join(ch for ch in name.upper() if ch.isalpha())[:2] or "PH"
    tail = ip.replace(":", ".").split(".")[-1][-3:] if ip else "0"
    return (letters + tail)[:5]

def destination(domain):
    d = domain.upper()
    for p in ("WWW.", "M."):
        if d.startswith(p): d = d[len(p):]
    return d

# --- DRAW ---
BG     = (6, 6, 8)
TILE   = (30, 30, 33)
SPLIT  = (10, 10, 12)
AMBER  = (255, 196, 54)
RED    = (255, 82, 70)
DIM    = (120, 120, 128)
WHITE  = (232, 232, 236)
TW, TH, GAP = 13, 22, 1          # one flap tile
COLS = [("TIME", 5), ("FLIGHT", 5), ("DESTINATION", 15), ("STATUS", 6)]
M = 9

# Each flap (character + colour) is drawn once and pasted after that: the
# board is ~280 tiles, and pasting a small image is much cheaper than text.
_tiles = {}

def tile(ch, color):
    key = (ch, color)
    t = _tiles.get(key)
    if t is None:
        t = Image.new("RGB", (TW, TH), TILE)
        d = ImageDraw.Draw(t)
        if ch.strip():
            d.text((TW / 2, TH / 2 + 1), ch, fill=color, font=flap_font, anchor="mm")
        d.line((0, TH // 2, TW, TH // 2), fill=SPLIT)
        _tiles[key] = t
    return t

def flaps(img, x, y, s, n, color):
    s = s[:n].ljust(n)
    for i, ch in enumerate(s):
        img.paste(tile(ch, color), (x + i * (TW + GAP), y))
    return x + n * (TW + GAP)

def draw_screen(stats):
    cpu, temp = get_cpu(), get_temp()
    ALERT = cpu > 80 or temp > 70
    rows = get_departures()

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # header: title + clock in flaps
    text(draw, M, 6, "DEPARTURES", AMBER, font_title)
    text(draw, M + text_w(draw, "DEPARTURES", font_title) + 10, 12, "·  " + get_hostname(), DIM, font)
    flaps(img, WIDTH - M - 5 * (TW + GAP), 6, datetime.now().strftime("%H:%M"), 5, WHITE)

    # column labels
    y = 38
    x = M
    for label, n in COLS:
        text(draw, x, y, label, DIM, font_label)
        x += n * (TW + GAP) + 8

    # rows
    y = 54
    for i in range(BOARD_ROWS):
        x = M
        if i < len(rows):
            ts, ip, name, domain = rows[i]
            cells = [datetime.fromtimestamp(ts).strftime("%H:%M"), flight_code(ip, name),
                     destination(domain), "DENIED"]
        else:
            cells = ["", "", "", ""]
        for (label, n), s in zip(COLS, cells):
            x = flaps(img, x, y, s, n, RED if label == "STATUS" else AMBER) + 8
        y += TH + 3

    # footer
    draw.line((M, 285, WIDTH - M, 285), fill=TILE)
    if stats:
        left = f"{stats['blocked']:,} flights denied today  ·  {stats['blocked_pct']:.1f}% of {stats['total']:,}"
    else:
        left = "no flight data"
    text(draw, M, 294, left, WHITE, font)
    text_right(draw, WIDTH - M, 294, f"{temp:.0f}°C  CPU {cpu:.0f}%", RED if ALERT else DIM, font)
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
get_departures = _every(STATS_TTL, get_departures)

# --- LOOP ---
if __name__ == "__main__":
    while True:
        write_fb(draw_screen(get_stats()))
        time.sleep(UPDATE_INTERVAL)
