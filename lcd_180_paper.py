#!/usr/bin/env python3
"""Pi-hole LCD (480x320): newspaper front page. 'The Daily Block' headline, latest blocked, system 'weather'."""
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
SERIF_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
SERIF      = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
font_mast  = ImageFont.truetype(SERIF_BOLD, 28)
font_head  = ImageFont.truetype(SERIF_BOLD, 27)
font_sub   = ImageFont.truetype(SERIF, 13)
font_sect  = ImageFont.truetype(SERIF_BOLD, 12)
font_body  = ImageFont.truetype(SERIF, 12)
font_small = ImageFont.truetype(SERIF, 10)

# --- DRAW ---
PAPER = (243, 238, 226)
INK   = (30, 28, 26)
GREY  = (112, 106, 98)
RULE  = (70, 66, 60)
RED   = (168, 32, 32)
M = 14

def rule(draw, y, double=False):
    draw.line((M, y, WIDTH - M, y), fill=RULE)
    if double:
        draw.line((M, y + 3, WIDTH - M, y + 3), fill=RULE)

def forecast(v, fair, warm):
    """Tongue-in-cheek 'weather' for a system reading."""
    return "fair" if v < fair else "warm" if v < warm else "stormy"

def draw_screen(stats):
    cpu, temp, mem = get_cpu(), get_temp(), get_mem()
    ALERT = cpu > 80 or temp > 70
    now = datetime.now()

    img = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(img)

    # MASTHEAD
    text_center(draw, WIDTH // 2, 6, "THE DAILY BLOCK", INK, font_mast)
    rule(draw, 42, double=True)
    text(draw, M, 50, f"Vol. {get_uptime_short()}", GREY, font_small)
    text_center(draw, WIDTH // 2, 50, now.strftime("%A, %d %B %Y"), INK, font_small)
    text_right(draw, WIDTH - M, 50, f"{get_hostname()} edition", GREY, font_small)
    rule(draw, 65)

    # HEADLINE
    head = f"{stats['blocked']:,} ads stopped today" if stats else "Presses halted: no data"
    text_center(draw, WIDTH // 2, 72, fit(draw, head, font_head, WIDTH - 2 * M), INK, font_head)
    if ALERT:
        sub = f"EXTRA: system running hot, CPU {cpu:.0f}% at {temp:.0f}°C"
        text_center(draw, WIDTH // 2, 108, sub, RED, font_sub)
    elif stats:
        sub = (f"{stats['blocked_pct']:.1f}% of {stats['total']:,} lookups turned away "
               f"across {stats['clients']} devices")
        text_center(draw, WIDTH // 2, 108, fit(draw, sub, font_sub, WIDTH - 2 * M), GREY, font_sub)
    rule(draw, 130)

    # LEFT COLUMN: latest blocked
    mid = 292
    draw.line((mid, 138, mid, HEIGHT - 32), fill=RULE)
    text(draw, M, 138, "MOST WANTED", INK, font_sect)
    text(draw, M, 154, "Latest blocked domains", GREY, font_small)
    y = 172
    for d in (stats["last_blocked"][:6] if stats else []):
        text(draw, M, y, "•", RED, font_body)
        text(draw, M + 12, y, fit(draw, d, font_body, mid - M - 22), INK, font_body)
        y += 19

    # RIGHT COLUMN: system "weather"
    rx = mid + 12
    text(draw, rx, 138, "WEATHER", INK, font_sect)
    text(draw, rx, 154, "Conditions on the Pi", GREY, font_small)
    y = 172
    for label, val, fc in (("CPU", f"{cpu:.0f}%", forecast(cpu, 60, 80)),
                           ("Temp", f"{temp:.0f}°C", forecast(temp, 60, 70)),
                           ("Memory", f"{mem:.0f}%", forecast(mem, 70, 90))):
        text(draw, rx, y, label, INK, font_body)
        text_right(draw, WIDTH - M, y, val, INK, font_body)
        text(draw, rx, y + 15, fc, RED if fc == "stormy" else GREY, font_small)
        y += 34

    # FOOTER
    rule(draw, HEIGHT - 26)
    last = f"  ·  last query {stats['last']}" if stats else ""
    text(draw, M, HEIGHT - 20, f"Printed {now:%H:%M}{last}", GREY, font_small)
    text_right(draw, WIDTH - M, HEIGHT - 20, f"{get_ip()}  ·  price: free", GREY, font_small)
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
