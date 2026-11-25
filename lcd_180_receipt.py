#!/usr/bin/env python3
"""Pi-hole LCD (480x320): till receipt. Today's most-blocked domains itemised on thermal paper, with totals and a barcode."""
import time, sqlite3, socket, psutil, zlib
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
MONO_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
font_label = ImageFont.truetype(FONT_PATH, 10)
font_side  = ImageFont.truetype(FONT_PATH, 12)
font_sideb = ImageFont.truetype(FONT_PATH, 20)
mono       = ImageFont.truetype(MONO_PATH, 11)
mono_big   = ImageFont.truetype(MONO_PATH, 16)

# --- TOP BLOCKED TODAY ---
ITEMS = 6

def get_items():
    """Today's most-blocked domains: [(domain, count)]."""
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("""
        SELECT domain, COUNT(*) FROM queries
        WHERE status=1 AND timestamp >= strftime('%s','now','start of day')
        GROUP BY domain ORDER BY 2 DESC LIMIT ?;
        """, (ITEMS,))
        rows = c.fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(e); return []

# --- DRAW ---
BG     = (34, 28, 26)          # counter top
PAPER  = (244, 240, 228)
INK    = (40, 38, 44)
FADED  = (130, 126, 120)
SIDE   = (150, 136, 126)
SIDEHI = (236, 226, 214)
HOT    = (239, 83, 80)
PX0, PX1 = 112, 368            # paper edges
LINE_H = 13

def row(draw, y, left, right="", f=mono, c=INK):
    """One receipt line: left text, right-aligned amount, dotted-free like a real till."""
    width = int((PX1 - PX0 - 20) / f.getlength("M"))
    left = left[: max(1, width - len(right) - 1)]
    draw.text((PX0 + 10, y), left, fill=c, font=f)
    if right:
        text_right(draw, PX1 - 10, y, right, c, f)

def rule(draw, y, ch="-"):
    n = int((PX1 - PX0 - 20) / mono.getlength(ch))
    draw.text((PX0 + 10, y), ch * n, fill=FADED, font=mono)

def barcode(draw, x0, x1, y, h, seed):
    """Fake but stable barcode: bar widths come from a checksum of the seed."""
    bits, x = zlib.crc32(seed.encode()), x0
    i = 0
    while x < x1:
        v = (bits >> (i % 32)) & 3
        w = 1 + v % 3
        if i % 2 == 0:
            draw.rectangle((x, y, min(x1, x + w - 1), y + h), fill=INK)
        x += w + (1 if i % 2 else 0)
        i += 1
        if i % 32 == 0:
            bits = zlib.crc32(str(bits).encode())

def side_stat(draw, x, y, label, value, color, right=False):
    put = text_right if right else text
    put(draw, x, y, label, SIDE, font_label)
    put(draw, x, y + 12, value, color, font_sideb)

def draw_screen(stats):
    cpu, temp, mem = get_cpu(), get_temp(), get_mem()
    ALERT = cpu > 80 or temp > 70
    now = datetime.now()
    items = get_items()

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # paper with a torn, zig-zag bottom edge
    bottom = 304
    draw.rectangle((PX0, 0, PX1, bottom), fill=PAPER)
    teeth = [(PX0, bottom)]
    for i, x in enumerate(range(PX0, PX1 + 1, 6)):
        teeth.append((x, bottom + (6 if i % 2 else 0)))
    teeth.append((PX1, bottom))
    draw.polygon(teeth, fill=PAPER)

    cx = (PX0 + PX1) // 2
    y = 8
    draw.text((cx, y), "PI-HOLE MARKET", fill=INK, font=mono_big, anchor="mt"); y += 21
    draw.text((cx, y), f"{get_hostname()}  ·  {get_ip()}", fill=FADED, font=mono, anchor="mt"); y += LINE_H
    row(draw, y, now.strftime("%d/%m/%Y"), now.strftime("%H:%M"), c=FADED); y += LINE_H
    rule(draw, y); y += LINE_H
    row(draw, y, "BLOCKED ITEM", "QTY"); y += LINE_H
    for i in range(ITEMS):
        if i < len(items):
            d, n = items[i]
            row(draw, y, d, f"{n:,}")
        y += LINE_H
    rule(draw, y); y += LINE_H
    if stats:
        row(draw, y, "QUERIES", f"{stats['total']:,}"); y += LINE_H
        row(draw, y, "ALLOWED", f"{stats['total'] - stats['blocked']:,}"); y += LINE_H
        row(draw, y, "BLOCK RATE", f"{stats['blocked_pct']:.1f}%"); y += LINE_H
        rule(draw, y, "="); y += LINE_H
        row(draw, y, "TOTAL BLOCKED", f"{stats['blocked']:,}", f=mono_big); y += 20
    else:
        row(draw, y, "DATABASE UNAVAILABLE"); y += 4 * LINE_H + 7
    draw.text((cx, y), "THANK YOU  ·  NO ADS SOLD", fill=FADED, font=mono, anchor="mt"); y += LINE_H + 3
    seed = f"{now:%Y%m%d}{stats['total'] if stats else 0}"
    barcode(draw, PX0 + 40, PX1 - 40, y, bottom - y - 6, seed)

    # left: the "printer" (system)
    side_stat(draw, 12, 14, "PRINTER TEMP", f"{temp:.0f}°C", HOT if temp > 70 else SIDEHI)
    side_stat(draw, 12, 62, "CPU", f"{cpu:.0f}%", HOT if cpu > 80 else SIDEHI)
    side_stat(draw, 12, 110, "MEMORY", f"{mem:.0f}%", SIDEHI)
    side_stat(draw, 12, 158, "UP", get_uptime_short(), SIDEHI)
    if ALERT:
        text(draw, 12, 214, "RUNNING HOT", HOT, font_side)

    # right: the "shop" (Pi-hole)
    R = WIDTH - 12
    if stats:
        side_stat(draw, R, 14, "CUSTOMERS", f"{stats['clients']}", SIDEHI, right=True)
        basket = stats["total"] // stats["clients"] if stats["clients"] else 0
        side_stat(draw, R, 62, "AVG BASKET", f"{basket:,}", SIDEHI, right=True)
        side_stat(draw, R, 110, "LAST SALE", stats["last"], SIDEHI, right=True)
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
get_items = _every(120, get_items)
get_ip = _every(300, get_ip)

# --- LOOP ---
if __name__ == "__main__":
    while True:
        write_fb(draw_screen(get_stats()))
        time.sleep(UPDATE_INTERVAL)
