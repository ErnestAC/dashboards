#!/usr/bin/env python3
"""Pi-hole LCD (480x320): engineering blueprint. Queries per hour drawn as a dimensioned curve, today's blocked share as a hatched part, system data in the title block."""
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
MONO_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
mono_xs = ImageFont.truetype(MONO_PATH, 9)
mono_s  = ImageFont.truetype(MONO_PATH, 10)
mono    = ImageFont.truetype(MONO_PATH, 12)
mono_l  = ImageFont.truetype(MONO_PATH, 14)

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

# --- DRAW ---
PAPER = (20, 60, 128)
MINOR = (30, 72, 142)
MAJOR = (42, 88, 158)
INK   = (232, 240, 255)
FAINT = (150, 180, 225)
WARN  = (255, 200, 90)

def arrow(draw, x, y, dx, dy, c=INK, s=5):
    """Arrowhead with its tip at (x, y), pointing along (dx, dy) (unit axis)."""
    px, py = -dy, dx
    draw.polygon([(x, y), (x - dx * s + px * s * 0.45, y - dy * s + py * s * 0.45),
                  (x - dx * s - px * s * 0.45, y - dy * s - py * s * 0.45)], fill=c)

def hdim(draw, x0, x1, y, label, c=INK):
    """Horizontal dimension: line with arrows at both ends, label centred above."""
    draw.line((x0, y, x1, y), fill=c)
    arrow(draw, x0, y, -1, 0, c); arrow(draw, x1, y, 1, 0, c)
    w = text_w(draw, label, mono_s)
    if w + 8 < x1 - x0:
        draw.rectangle(((x0 + x1 - w) // 2 - 3, y - 6, (x0 + x1 + w) // 2 + 3, y + 6), fill=PAPER)
        text_center(draw, (x0 + x1) // 2, y - 6, label, c, mono_s)

def hatch(img, box, c, step=6):
    """Fill box with 45° hatching."""
    x0, y0, x1, y1 = box
    layer = Image.new("RGB", (x1 - x0, y1 - y0), PAPER)
    d = ImageDraw.Draw(layer)
    h = y1 - y0
    for k in range(-h, x1 - x0, step):
        d.line((k, h, k + h, 0), fill=c)
    img.paste(layer, (x0, y0))

def draw_screen(stats):
    cpu, temp, mem = get_cpu(), get_temp(), get_mem()
    now = datetime.now()
    img = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(img)

    for x in range(0, WIDTH, 10):
        draw.line((x, 0, x, HEIGHT), fill=MAJOR if x % 50 == 0 else MINOR)
    for y in range(0, HEIGHT, 10):
        draw.line((0, y, WIDTH, y), fill=MAJOR if y % 50 == 0 else MINOR)
    draw.rectangle((4, 4, WIDTH - 5, HEIGHT - 5), outline=INK, width=2)

    # --- queries per hour, as a plotted curve ---
    hours = get_hourly()
    cx0, cy0, cx1, cy1 = 44, 30, 420, 136
    top = max((n for n, _ in hours), default=0) or 1
    text(draw, 16, 12, "FIG. 1  QUERIES / HOUR  (LAST 24 H)", INK, mono)
    draw.line((cx0, cy1, cx1 + 6, cy1), fill=INK)          # axes
    draw.line((cx0, cy0 - 6, cx0, cy1), fill=INK)
    arrow(draw, cx1 + 10, cy1, 1, 0); arrow(draw, cx0, cy0 - 10, 0, -1)
    step = (cx1 - cx0) / 23
    first_hour = (now.hour + 1) % 24
    for i in range(24):
        x = cx0 + i * step
        draw.line((x, cy1, x, cy1 + (5 if i % 6 == 0 else 2)), fill=INK)
        if i % 6 == 0:
            text_center(draw, int(x), cy1 + 6, f"{(first_hour + i) % 24:02d}", FAINT, mono_xs)
    pts = [(cx0 + i * step, cy1 - n / top * (cy1 - cy0)) for i, (n, _) in enumerate(hours)]
    bpts = [(cx0 + i * step, cy1 - b / top * (cy1 - cy0)) for i, (_, b) in enumerate(hours)]
    for i in range(0, len(bpts) - 1):                       # blocked curve, dashed
        if i % 2 == 0: draw.line((bpts[i], bpts[i + 1]), fill=WARN, width=1)
    draw.line(pts, fill=INK, width=2)
    for p in pts[::2]:
        draw.ellipse((p[0] - 2, p[1] - 2, p[0] + 2, p[1] + 2), outline=INK, fill=PAPER)

    # peak, with a vertical dimension on the right
    pk = max(range(24), key=lambda i: hours[i][0])
    px, py = pts[pk]
    draw.line((px + 4, py, cx1 + 22, py), fill=FAINT)        # extension line
    vx = cx1 + 18
    draw.line((vx, py, vx, cy1), fill=INK)
    arrow(draw, vx, py, 0, -1); arrow(draw, vx, cy1, 0, 1)
    lbl = f"{hours[pk][0]}"
    text(draw, vx + 5, (py + cy1) // 2 - 12, "PEAK", FAINT, mono_xs)
    text(draw, vx + 5, (py + cy1) // 2, lbl, INK, mono_s)
    text_right(draw, WIDTH - 16, 14, "- - BLOCKED", WARN, mono_xs)
    text_right(draw, WIDTH - 16 - text_w(draw, "- - BLOCKED  ", mono_xs), 14, "—— ALL", INK, mono_xs)

    # --- today's split, as a hatched part with dimensions ---
    text(draw, 16, 160, "FIG. 2  SECTION A-A  (TODAY)", INK, mono)
    bx0, bx1, by0, by1 = 30, 450, 196, 216
    if stats and stats["total"]:
        split = bx0 + round((bx1 - bx0) * stats["blocked"] / stats["total"])
        hatch(img, (bx0, by0, split, by1), WARN)
        draw = ImageDraw.Draw(img)
        draw.rectangle((bx0, by0, bx1, by1), outline=INK, width=2)
        draw.line((split, by0, split, by1), fill=INK, width=2)
        for x in (bx0, split, bx1):
            draw.line((x, by0 - 14, x, by0 - 3), fill=FAINT)
        hdim(draw, bx0, split, by0 - 9, f"{stats['blocked']:,} BLK", WARN)
        hdim(draw, split, bx1, by0 - 9, f"{stats['total'] - stats['blocked']:,} OK")
        for x in (bx0, bx1):
            draw.line((x, by1 + 3, x, by1 + 14), fill=FAINT)
        hdim(draw, bx0, bx1, by1 + 9, f"{stats['total']:,} QUERIES  ({stats['blocked_pct']:.1f}% BLOCKED)")
    else:
        draw.rectangle((bx0, by0, bx1, by1), outline=INK, width=2)
        text_center(draw, WIDTH // 2, by0 + 4, "NO DATA", INK, mono_s)

    # --- notes and title block ---
    ty = 238
    draw.line((4, ty, WIDTH - 5, ty), fill=INK, width=2)
    text(draw, 12, ty + 6, "NOTES:", INK, mono_s)
    notes = [f"1. {stats['clients']} CLIENTS SERVED TODAY." if stats else "1. DATABASE UNAVAILABLE.",
             f"2. LAST QUERY AT {stats['last']}." if stats else "2. —",
             f"3. UPTIME {get_uptime_short().upper()}.  IP {get_ip()}"]
    for i, n in enumerate(notes):
        text(draw, 12, ty + 20 + i * 13, n, FAINT, mono_xs)

    tb = 292
    draw.line((tb, ty, tb, HEIGHT - 5), fill=INK, width=2)
    rows = [ty, ty + 26, ty + 52, HEIGHT - 5]
    for y in rows[1:-1]:
        draw.line((tb, y, WIDTH - 5, y), fill=INK)
    mid = tb + 88
    draw.line((mid, rows[1], mid, HEIGHT - 5), fill=INK)
    text(draw, tb + 6, ty + 3, "PI-HOLE DNS FILTER", INK, mono_l)
    text(draw, tb + 6, ty + 16 - 1, f"DWG {get_hostname().upper()}-001", FAINT, mono_xs)
    alert = cpu > 80 or temp > 70
    cells = [("DATE", now.strftime("%d %b %Y").upper()), ("TIME", now.strftime("%H:%M")),
             ("CPU / MEM", f"{cpu:.0f}% / {mem:.0f}%"), ("TEMP", f"{temp:.1f}°C")]
    for i, (k, v) in enumerate(cells):
        x = tb + 6 if i % 2 == 0 else mid + 6
        y = rows[1 + i // 2]
        text(draw, x, y + 2, k, FAINT, mono_xs)
        text(draw, x, y + 12, v, WARN if alert and i >= 2 else INK, mono_s)
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
