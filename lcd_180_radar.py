#!/usr/bin/env python3
"""Pi-hole LCD (480x320): radar scope. Devices active in the last 15 min as blips (busier = nearer the centre, red = hit a blocked domain)."""
import time, sqlite3, socket, psutil, math, zlib
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
font       = ImageFont.truetype(FONT_PATH, 14)
font_title = ImageFont.truetype(FONT_PATH, 16)
font_stat  = ImageFont.truetype(FONT_PATH, 26)

# --- ACTIVE DEVICES ---
WINDOW = 15 * 60

def get_contacts():
    """Devices seen in the last WINDOW seconds: [(ip, name, queries, blocked)], busiest first."""
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("""
        SELECT client, COUNT(*), SUM(CASE WHEN status=1 THEN 1 ELSE 0 END)
        FROM queries WHERE timestamp >= ?
        GROUP BY client ORDER BY 2 DESC;
        """, (int(time.time()) - WINDOW,))
        rows = c.fetchall()
        names = {}
        try:
            c.execute("SELECT ip, name FROM client_by_id WHERE name IS NOT NULL AND name != '';")
            names = dict(c.fetchall())
        except sqlite3.Error:
            pass
        conn.close()
        return [(ip, names.get(ip, ip), n, b or 0) for ip, n, b in rows]
    except Exception as e:
        print(e); return []

# --- DRAW ---
BG     = (2, 10, 6)
SCOPE  = (4, 22, 12)
RING   = (18, 70, 38)
SWEEP  = (10, 48, 26)
GREEN  = (90, 255, 140)
RED    = (255, 80, 70)
FG     = (200, 240, 210)
DIM    = (80, 140, 100)
CX, CY, R = 158, 160, 148
SWEEP_DPS = 24                    # sweep speed, degrees per second
M = 10

def blip_pos(ip, n, top):
    """Stable bearing from the address; busier devices sit nearer the centre."""
    bearing = zlib.crc32(ip.encode()) % 360
    closeness = math.log1p(n) / math.log1p(top) if top else 0
    dist = R * (0.92 - 0.72 * closeness)
    a = math.radians(bearing)
    return CX + dist * math.cos(a), CY + dist * math.sin(a), bearing

def shade(color, k):
    return tuple(int(c * k + b * (1 - k)) for c, b in zip(color, SCOPE))

def draw_screen(stats):
    cpu, temp = get_cpu(), get_temp()
    ALERT = cpu > 80 or temp > 70
    contacts = get_contacts()
    sweep = (time.time() * SWEEP_DPS) % 360

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # scope face, sweep wedge, rings, crosshair
    draw.ellipse((CX - R, CY - R, CX + R, CY + R), fill=SCOPE, outline=RING, width=2)
    draw.pieslice((CX - R, CY - R, CX + R, CY + R), sweep - 50, sweep, fill=SWEEP)
    for k in (1 / 3, 2 / 3):
        r = R * k
        draw.ellipse((CX - r, CY - r, CX + r, CY + r), outline=RING)
    draw.line((CX - R, CY, CX + R, CY), fill=RING)
    draw.line((CX, CY - R, CX, CY + R), fill=RING)
    a = math.radians(sweep)
    draw.line((CX, CY, CX + R * math.cos(a), CY + R * math.sin(a)), fill=GREEN, width=2)

    # blips: brightest just after the sweep has passed, fading as it moves on
    top = contacts[0][2] if contacts else 0
    for ip, name, n, b in reversed(contacts):
        x, y, bearing = blip_pos(ip, n, top)
        behind = (sweep - bearing) % 360
        k = max(0.3, 1 - behind / 360)
        col = shade(RED if b else GREEN, k)
        s = 3 + min(4, int(math.log1p(n)))
        draw.ellipse((x - s, y - s, x + s, y + s), fill=col)
    placed = []
    for ip, name, n, b in contacts[:3]:                    # label the three busiest
        x, y, _ = blip_pos(ip, n, top)
        if any(abs(y - py) < 13 and abs(x - px) < 90 for px, py in placed):
            continue                                       # would overlap another label
        placed.append((x, y))
        text(draw, x + 8, y - 7, fit(draw, name, font_label, 90), DIM, font_label)

    # right-hand panel
    X0, X1 = 326, WIDTH - M
    text(draw, X0, 8, get_hostname(), FG, font_title)
    text_right(draw, X1, 10, datetime.now().strftime("%H:%M"), DIM, font)
    hostile = sum(1 for c in contacts if c[3])
    text(draw, X0, 34, "CONTACTS", DIM, font_label)
    text(draw, X0, 46, f"{len(contacts)}", GREEN, font_stat)
    text_right(draw, X1, 34, "HOSTILE", DIM, font_label)
    text_right(draw, X1, 46, f"{hostile}", RED if hostile else DIM, font_stat)

    y = 88
    text(draw, X0, y, "BUSIEST · LAST 15 MIN", DIM, font_label); y += 15
    for ip, name, n, b in contacts[:6]:
        draw.ellipse((X0, y + 4, X0 + 6, y + 10), fill=RED if b else GREEN)
        text(draw, X0 + 11, y, fit(draw, name, font_small, 100), FG, font_small)
        text_right(draw, X1, y, f"{n:,}", DIM, font_small)
        y += 17
    if not contacts:
        text(draw, X0, y, "no contacts", DIM, font_small)

    draw.line((X0, 222, X1, 222), fill=RING)
    if stats:
        text(draw, X0, 230, "BLOCKED", DIM, font_label)
        text(draw, X0, 242, f"{stats['blocked_pct']:.1f}%", RED, font_title)
        text_right(draw, X1, 230, "QUERIES", DIM, font_label)
        text_right(draw, X1, 242, f"{stats['total']:,}", FG, font_title)
    text(draw, X0, 276, "SYSTEM", DIM, font_label)
    text(draw, X0, 288, f"{temp:.0f}°C · CPU {cpu:.0f}%" + ("  HOT" if ALERT else ""),
         RED if ALERT else FG, font_small)
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
get_contacts = _every(STATS_TTL, get_contacts)

# --- LOOP ---
if __name__ == "__main__":
    while True:
        write_fb(draw_screen(get_stats()))
        time.sleep(UPDATE_INTERVAL)
