#!/usr/bin/env python3
"""Pi-hole LCD (480x320): week at a glance. One calendar card per day for the last 7 days: queries, blocked share and a stacked bar, today highlighted."""
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
font       = ImageFont.truetype(FONT_PATH, 13)
font_title = ImageFont.truetype(FONT_PATH, 18)
font_day   = ImageFont.truetype(FONT_PATH, 24)

# --- WEEK ---
def get_week():
    """Last 7 local days, oldest first: [(date, total, blocked)]."""
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("""
        SELECT date(timestamp, 'unixepoch', 'localtime') AS d, COUNT(*),
               SUM(CASE WHEN status=1 THEN 1 ELSE 0 END)
        FROM queries WHERE timestamp >= strftime('%s', 'now', 'localtime', 'start of day', '-6 days', 'utc')
        GROUP BY d;
        """)
        got = {d: (n, b or 0) for d, n, b in c.fetchall()}
        conn.close()
    except Exception as e:
        print(e); got = {}
    today = datetime.now().date()
    days = [datetime.fromordinal(today.toordinal() - k).date() for k in range(6, -1, -1)]
    return [(d, *got.get(d.isoformat(), (0, 0))) for d in days]

def short(n):
    return f"{n / 1000:.1f}k" if n >= 10000 else f"{n:,}"

# --- DRAW ---
BG    = (12, 14, 20)
PANEL = (24, 28, 38)
TODAY = (32, 40, 58)
TRACK = (38, 44, 58)
FG    = (232, 234, 240)
DIM   = (128, 136, 150)
RED   = (239, 83, 80)
BLUE  = (66, 165, 245)
GREEN = (102, 187, 106)

def draw_screen(stats):
    cpu, temp = get_cpu(), get_temp()
    now = datetime.now()
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    week = get_week()
    text(draw, 12, 9, "This week", FG, font_title)
    text(draw, 12 + text_w(draw, "This week", font_title) + 10, 15, get_hostname(), DIM, font_small)
    span = f"{week[0][0].strftime('%d %b')} – {week[-1][0].strftime('%d %b')}  ·  {now.strftime('%H:%M')}"
    text_right(draw, WIDTH - 12, 15, span, DIM, font_small)

    # day cards
    gap, x0, y0, y1 = 6, 8, 40, 256
    cw = (WIDTH - 2 * x0 - 6 * gap) / 7
    top = max((n for _, n, _ in week), default=0) or 1
    for i, (d, n, b) in enumerate(week):
        bx0 = round(x0 + i * (cw + gap)); bx1 = round(bx0 + cw)
        is_today = i == 6
        panel(draw, (bx0, y0, bx1, y1), TODAY if is_today else PANEL, 7)
        if is_today:
            draw.rounded_rectangle((bx0, y0, bx1, y1), radius=7, outline=BLUE, width=2) \
                if hasattr(draw, "rounded_rectangle") else draw.rectangle((bx0, y0, bx1, y1), outline=BLUE, width=2)
        mx = (bx0 + bx1) // 2
        text_center(draw, mx, y0 + 7, "TODAY" if is_today else d.strftime("%a").upper(),
                    BLUE if is_today else DIM, font_label)
        text_center(draw, mx, y0 + 20, d.strftime("%d").lstrip("0"), FG, font_day)

        # stacked bar: allowed (blue) under blocked (red)
        bt, bb, bw = y0 + 56, y1 - 46, 18
        draw.rectangle((mx - bw // 2, bt, mx + bw // 2, bb), fill=TRACK)
        h = round(n / top * (bb - bt)); hb = round(b / top * (bb - bt))
        if h:
            draw.rectangle((mx - bw // 2, bb - h, mx + bw // 2, bb), fill=BLUE)
        if hb:
            draw.rectangle((mx - bw // 2, bb - h, mx + bw // 2, bb - h + hb), fill=RED)

        text_center(draw, mx, y1 - 40, short(n) if n else "—", FG, font)
        pct = f"{b / n * 100:.0f}%" if n else ""
        text_center(draw, mx, y1 - 22, pct, RED, font_small)

    # week totals
    tot = sum(n for _, n, _ in week); blk = sum(b for _, _, b in week)
    busiest = max(week, key=lambda r: r[1])
    y = 266
    panel(draw, (8, y, WIDTH - 8, y + 44), PANEL, 7)
    cols = [("7-DAY QUERIES", f"{tot:,}", FG), ("BLOCKED", f"{blk:,} · {blk / tot * 100:.1f}%" if tot else "—", RED),
            ("BUSIEST", busiest[0].strftime("%A") if busiest[1] else "—", BLUE),
            ("CPU · TEMP", f"{cpu:.0f}% · {temp:.0f}°C", RED if cpu > 80 or temp > 70 else GREEN)]
    xs = [18, 130, 262, 372]
    for (k, v, c), x in zip(cols, xs):
        text(draw, x, y + 7, k, DIM, font_label)
        text(draw, x, y + 21, v, c, font)
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
get_week = _every(STATS_TTL, get_week)

# --- LOOP ---
if __name__ == "__main__":
    while True:
        write_fb(draw_screen(get_stats()))
        time.sleep(UPDATE_INTERVAL)
