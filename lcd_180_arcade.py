#!/usr/bin/env python3
"""Pi-hole LCD (480x320): pixel-art arcade scoreboard. Ads blocked as the high score, system stats as health bars."""
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
# drawn on a 120x80 canvas without anti-aliasing, then scaled up 4x for chunky pixels
MONO_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
px_small = ImageFont.truetype(MONO_PATH, 7)
px_text  = ImageFont.truetype(MONO_PATH, 8)
px_score = ImageFont.truetype(MONO_PATH, 15)

# --- DRAW ---
SCALE = 4
CW, CH = WIDTH // SCALE, HEIGHT // SCALE      # 120 x 80 canvas
BLACK  = (0, 0, 0)
WHITE  = (240, 240, 240)
YELLOW = (255, 214, 0)
CYAN   = (0, 220, 255)
RED    = (255, 60, 60)
GREEN  = (60, 220, 90)
DGREY  = (60, 60, 70)

# an original 7x7 pixel shield, one per 10% of queries blocked
SHIELD = [".#####.",
          "#######",
          "###.###",
          "##...##",
          ".##.##.",
          "..###..",
          "...#..."]

def tile(draw, x, y, color):
    for j, row in enumerate(SHIELD):
        for i, ch in enumerate(row):
            if ch == "#":
                draw.point((x + i, y + j), fill=color)

def pbar(draw, x, y, w, pct, color):
    """Segmented pixel bar."""
    segs = w // 3
    on = round(max(0, min(100, pct)) / 100 * segs)
    for i in range(segs):
        draw.rectangle((x + i * 3, y, x + i * 3 + 1, y + 3), fill=color if i < on else DGREY)

def draw_screen(stats):
    cpu, temp, mem = get_cpu(), get_temp(), get_mem()
    ALERT = cpu > 80 or temp > 70
    blink = int(time.time()) % 2 == 0

    small = Image.new("RGB", (CW, CH), BLACK)
    d = ImageDraw.Draw(small)
    d.fontmode = "1"                          # no anti-aliasing: crisp pixels

    # top line: player / clock
    d.text((2, 1), "1UP " + get_hostname()[:8].upper(), fill=CYAN, font=px_small)
    clock = datetime.now().strftime("%H:%M")
    d.text((CW - 2 - d.textbbox((0, 0), clock, font=px_small)[2], 1), clock, fill=CYAN, font=px_small)

    # score
    score = stats["blocked"] if stats else 0
    label = "ADS BLOCKED"
    d.text(((CW - d.textbbox((0, 0), label, font=px_text)[2]) // 2, 10), label, fill=YELLOW, font=px_text)
    s = f"{score:07d}"
    d.text(((CW - d.textbbox((0, 0), s, font=px_score)[2]) // 2, 19), s, fill=WHITE, font=px_score)

    # shields: one per ~10% blocked
    n = max(1, round((stats["blocked_pct"] if stats else 0) / 10))
    x0 = (CW - n * 10) // 2 + 1
    for i in range(n):
        tile(d, x0 + i * 10, 38, GREEN)

    # stats
    if stats:
        d.text((2, 48), f"HIT {stats['blocked_pct']:4.1f}%", fill=WHITE, font=px_small)
        d.text((66, 48), f"PLAYERS {stats['clients']:>2}", fill=WHITE, font=px_small)
    # health bars
    d.text((2, 57), "CPU", fill=YELLOW, font=px_small)
    pbar(d, 20, 59, 36, cpu, RED if cpu > 80 else GREEN)
    d.text((62, 57), "MEM", fill=YELLOW, font=px_small)
    pbar(d, 80, 59, 36, mem, RED if mem > 90 else GREEN)
    d.text((2, 66), "TMP", fill=YELLOW, font=px_small)
    pbar(d, 20, 68, 36, temp / 85 * 100, RED if temp > 70 else GREEN)
    d.text((62, 66), f"LVL {get_uptime_short().split()[0].upper()}", fill=WHITE, font=px_small)

    if ALERT and blink:
        msg = "OVERHEAT!"
        d.rectangle((0, 74, CW, CH), fill=RED)
        d.text(((CW - d.textbbox((0, 0), msg, font=px_small)[2]) // 2, 73), msg, fill=WHITE, font=px_small)

    return small.resize((CW * SCALE, CH * SCALE), Image.NEAREST)

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
