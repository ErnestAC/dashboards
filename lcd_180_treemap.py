#!/usr/bin/env python3
"""Pi-hole LCD (480x320): domain treemap. Today's busiest domains as tiles sized by query count, blocked ones in red, allowed ones in teal."""
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
font       = ImageFont.truetype(FONT_PATH, 14)
font_title = ImageFont.truetype(FONT_PATH, 16)

# --- DOMAINS ---
TOP_N = 16

def get_domains():
    """Today's TOP_N domains: [(domain, queries, blocked)], busiest first."""
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("""
        SELECT domain, COUNT(*), SUM(CASE WHEN status=1 THEN 1 ELSE 0 END)
        FROM queries WHERE timestamp >= strftime('%s','now','start of day')
        GROUP BY domain ORDER BY 2 DESC LIMIT ?;
        """, (TOP_N,))
        rows = [(d, n, b or 0) for d, n, b in c.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(e); return []

# --- SQUARIFIED TREEMAP ---
def _worst(row, side):
    s = sum(row)
    return max(max(side * side * r / (s * s), (s * s) / (side * side * r)) for r in row)

def squarify(values, x, y, w, h):
    """Rectangles (x0, y0, x1, y1) for values (sorted, largest first) filling the box."""
    total = sum(values)
    if not total: return []
    areas = [v * w * h / total for v in values]
    out, i = [], 0
    while i < len(areas):
        side = min(w, h)
        row = [areas[i]]; i += 1
        while i < len(areas) and _worst(row + [areas[i]], side) <= _worst(row, side):
            row.append(areas[i]); i += 1
        s = sum(row)
        if w >= h:                                  # lay the row out as a column on the left
            cw = s / h; yy = y
            for a in row:
                out.append((x, yy, x + cw, yy + a / cw)); yy += a / cw
            x += cw; w -= cw
        else:                                       # or as a row on top
            rh = s / w; xx = x
            for a in row:
                out.append((xx, y, xx + a / rh, y + rh)); xx += a / rh
            y += rh; h -= rh
    return out

# --- DRAW ---
BG   = (11, 13, 18)
FG   = (240, 242, 246)
DIM  = (130, 138, 150)
RED  = (229, 72, 77)
TEAL = (38, 166, 154)

def shade(c, k):
    """Darken c towards the background: k=1 full colour, k=0 background."""
    return tuple(int(v * k + b * (1 - k)) for v, b in zip(c, BG))

def draw_screen(stats):
    cpu, temp = get_cpu(), get_temp()
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    text(draw, 10, 8, "Top domains today", FG, font_title)
    lx = WIDTH - 10
    for lbl, c in (("allowed", TEAL), ("blocked", RED)):
        lx -= text_w(draw, lbl, font_label)
        text(draw, lx, 13, lbl, DIM, font_label)
        lx -= 14; draw.rectangle((lx, 13, lx + 9, 22), fill=c); lx -= 12

    doms = get_domains()
    X0, Y0, X1, Y1 = 8, 34, WIDTH - 8, 286
    rects = squarify([n for _, n, _ in doms], X0, Y0, X1 - X0, Y1 - Y0)
    top = doms[0][1] if doms else 1
    for (d, n, b), (x0, y0, x1, y1) in zip(doms, rects):
        base = RED if b * 2 > n else TEAL
        k = 0.45 + 0.55 * (n / top) ** 0.5
        box = (round(x0) + 1, round(y0) + 1, round(x1) - 1, round(y1) - 1)
        if box[2] <= box[0] or box[3] <= box[1]: continue
        panel(draw, box, shade(base, k), 4)
        bw, bh = box[2] - box[0], box[3] - box[1]
        if bw > 34 and bh > 18:
            f = font if bw > 120 and bh > 44 else font_small if bw > 60 else font_label
            text(draw, box[0] + 5, box[1] + 4, fit(draw, d, f, bw - 10), FG, f)
            if bh > 36:
                text(draw, box[0] + 5, box[1] + 4 + f.size + 3, f"{n:,}", shade(FG, 0.75), font_label)
    if not doms:
        text_center(draw, WIDTH // 2, 150, "no queries yet today", DIM, font)

    line = (f"{stats['total']:,} queries · {stats['blocked']:,} blocked ({stats['blocked_pct']:.1f}%)"
            if stats else "Pi-hole database unavailable")
    text(draw, 10, 296, line, FG, font_small)
    sysline = f"{get_hostname()} · CPU {cpu:.0f}% · {temp:.0f}°C"
    text_right(draw, WIDTH - 10, 296, sysline, RED if cpu > 80 or temp > 70 else DIM, font_small)
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
get_domains = _every(STATS_TTL, get_domains)

# --- LOOP ---
if __name__ == "__main__":
    while True:
        write_fb(draw_screen(get_stats()))
        time.sleep(UPDATE_INTERVAL)
