#!/usr/bin/env python3
"""Pi-hole LCD (480x320): waffle chart. 100 squares, one per percent of today's queries, the blocked ones lit red, with the top blocking device beside it."""
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
font_big   = ImageFont.truetype(FONT_PATH, 46)

# --- TOP CLIENT ---
def get_top_client():
    """The device with the most blocked queries today: (name, blocked, its total) or None."""
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("""
        SELECT client, SUM(CASE WHEN status=1 THEN 1 ELSE 0 END) AS b, COUNT(*)
        FROM queries WHERE timestamp >= strftime('%s','now','start of day')
        GROUP BY client ORDER BY b DESC LIMIT 1;
        """)
        row = c.fetchone()
        name = None
        if row:
            try:
                c.execute("SELECT name FROM client_by_id WHERE ip = ? AND name IS NOT NULL AND name != '';",
                          (row[0],))
                r = c.fetchone(); name = r[0] if r else None
            except sqlite3.Error:
                pass
        conn.close()
        return (name or row[0], row[1] or 0, row[2]) if row and row[1] else None
    except Exception as e:
        print(e); return None

# --- DRAW ---
BG    = (13, 14, 19)
PANEL = (24, 26, 34)
EMPTY = (40, 44, 56)
FG    = (236, 238, 242)
DIM   = (128, 134, 148)
RED   = (244, 67, 54)
GREEN = (102, 187, 106)
BLUE  = (79, 156, 255)

def draw_screen(stats):
    cpu, temp = get_cpu(), get_temp()
    now = datetime.now()
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    text(draw, 12, 9, get_hostname(), FG, font_title)
    text_right(draw, WIDTH - 12, 11, now.strftime("%a %d %b  %H:%M"), DIM, font_small)

    # 10 x 10 waffle, filled row by row from the bottom left
    pct = stats["blocked_pct"] if stats else 0
    lit = round(pct)
    S, G, X0, Y0 = 21, 3, 14, 40
    for k in range(100):
        r, col = 9 - k // 10, k % 10
        x, y = X0 + col * (S + G), Y0 + r * (S + G)
        panel(draw, (x, y, x + S, y + S), RED if k < lit else EMPTY, 3)

    # right column
    rx = X0 + 10 * (S + G) + 18
    text(draw, rx, 34, f"{pct:.1f}%" if stats else "—", RED, font_big)
    text(draw, rx + 2, 88, "of today's queries blocked", DIM, font_small)
    text(draw, rx + 2, 104, "each square = 1%", DIM, font_label)

    y = 132
    rows = [("Blocked", f"{stats['blocked']:,}", RED), ("Allowed", f"{stats['total'] - stats['blocked']:,}", GREEN),
            ("Clients", f"{stats['clients']}", BLUE)] if stats else []
    for k, v, c in rows:
        draw.rectangle((rx + 2, y + 4, rx + 9, y + 11), fill=c)
        text(draw, rx + 16, y, k, DIM, font)
        text_right(draw, WIDTH - 14, y, v, FG, font)
        y += 22

    top = get_top_client()
    panel(draw, (rx - 4, 204, WIDTH - 8, 276), PANEL, 7)
    text(draw, rx + 4, 211, "MOST BLOCKED DEVICE", DIM, font_label)
    if top:
        name, b, n = top
        text(draw, rx + 4, 226, fit(draw, name, font, WIDTH - 20 - rx), FG, font)
        line = f"{b:,} blocked · {b / n * 100:.0f}% of its queries"
        if text_w(draw, line, font_small) > WIDTH - 20 - rx:
            line = f"{b:,} blocked ({b / n * 100:.0f}%)"
        text(draw, rx + 4, 248, line, RED, font_small)
    else:
        text(draw, rx + 4, 228, "nothing blocked yet", DIM, font)

    alert = cpu > 80 or temp > 70
    line = f"CPU {cpu:.0f}%  ·  {temp:.0f}°C  ·  up {get_uptime_short()}  ·  {get_ip()}"
    text_center(draw, WIDTH // 2, 294, line, RED if alert else DIM, font_small)
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
get_top_client = _every(STATS_TTL, get_top_client)

# --- LOOP ---
if __name__ == "__main__":
    while True:
        write_fb(draw_screen(get_stats()))
        time.sleep(UPDATE_INTERVAL)
