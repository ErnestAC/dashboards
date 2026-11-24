#!/usr/bin/env python3
"""Pi-hole LCD (480x320): synthwave neon. Glowing text over a perspective grid; system line turns orange when hot."""
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

from PIL import ImageFilter, ImageChops

# --- FONTS ---
FONT_PATH = "/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf"
font_label = ImageFont.truetype(FONT_PATH, 11)
font       = ImageFont.truetype(FONT_PATH, 15)
font_title = ImageFont.truetype(FONT_PATH, 17)
font_stat  = ImageFont.truetype(FONT_PATH, 24)
font_big   = ImageFont.truetype(FONT_PATH, 76)

# --- DRAW ---
CYAN    = (0, 229, 255)
MAGENTA = (255, 41, 163)
YELLOW  = (255, 214, 0)
LAVENDER = (176, 150, 230)
ORANGE  = (255, 90, 40)
GRIDC   = (110, 20, 90)
HORIZON = 214

def tint(c, k=0.55):
    """Lighter core colour for the sharp layer drawn over the glow."""
    return tuple(int(v + (255 - v) * k) for v in c)

def background():
    img = Image.new("RGB", (WIDTH, HEIGHT))
    d = ImageDraw.Draw(img)
    for y in range(HORIZON):                       # sky gradient
        t = y / HORIZON
        d.line((0, y, WIDTH, y), fill=(int(22 - 18 * t), int(4 - 4 * t), int(42 - 30 * t)))
    d.rectangle((0, HORIZON, WIDTH, HEIGHT), fill=(8, 0, 18))
    vx = WIDTH // 2                                # perspective grid
    for i in range(-12, 13):
        d.line((vx + i * 18, HORIZON, vx + i * 90, HEIGHT), fill=GRIDC)
    y, step = HORIZON + 4, 4
    while y < HEIGHT:
        d.line((0, y, WIDTH, y), fill=GRIDC); y += step; step = int(step * 1.45) + 1
    return img

def draw_screen(stats):
    cpu, temp, mem = get_cpu(), get_temp(), get_mem()
    ALERT = cpu > 80 or temp > 70
    hot = ORANGE if ALERT else CYAN
    now = datetime.now()

    # Every neon element is drawn twice: once on a glow layer that gets
    # blurred and added to the background, then sharp on top.
    items = []   # (kind, args, colour)
    def T(x, y, s, f, c, anchor="la"): items.append(("t", (x, y, s, f, anchor), c))
    def L(box, c, w=2):                items.append(("l", (box, w), c))

    T(14, 10, get_hostname(), font_title, CYAN)
    T(WIDTH - 14, 10, now.strftime("%H:%M"), font_title, CYAN, "ra")
    T(WIDTH // 2, 44, "SYSTEM HOT" if ALERT else "BLOCKED TODAY", font_label, ORANGE if ALERT else LAVENDER, "ma")
    if stats:
        T(WIDTH // 2, 60, f"{stats['blocked_pct']:.1f}%", font_big, MAGENTA, "ma")
        cols = [(f"{stats['total']:,}", "QUERIES", CYAN),
                (f"{stats['blocked']:,}", "BLOCKED", MAGENTA),
                (f"{stats['clients']}", "CLIENTS", YELLOW)]
        for i, (val, label, c) in enumerate(cols):
            cx = WIDTH * (2 * i + 1) // 6
            T(cx, 148, val, font_stat, c, "ma")
            T(cx, 177, label, font_label, LAVENDER, "ma")
        if stats["last_blocked"]:
            T(WIDTH // 2, 194, "last blocked  " + stats["last_blocked"][0], font_label, LAVENDER, "ma")
    else:
        T(WIDTH // 2, 90, "NO DATA", font_big, MAGENTA, "ma")
    L((0, HORIZON, WIDTH, HORIZON), MAGENTA, 2)
    sysline = f"CPU {cpu:.0f}%    MEM {mem:.0f}%    {temp:.0f}°C    UP {get_uptime_short()}"
    T(WIDTH // 2, HEIGHT - 30, sysline, font, hot, "ma")

    def render(draw, sharp):
        for kind, a, c in items:
            col = tint(c) if sharp else c
            if kind == "t":
                x, y, s, f, anchor = a
                draw.text((x, y), s, fill=col, font=f, anchor=anchor)
            else:
                draw.line(a[0], fill=col, width=a[1])

    glow = Image.new("RGB", (WIDTH, HEIGHT))
    render(ImageDraw.Draw(glow), sharp=False)
    img = background()
    img = ImageChops.add(img, glow.filter(ImageFilter.GaussianBlur(8)))
    img = ImageChops.add(img, glow.filter(ImageFilter.GaussianBlur(3)))
    render(ImageDraw.Draw(img), sharp=True)
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
