import time
import sqlite3
from PIL import Image, ImageDraw, ImageFont

FB_DEV = "/dev/fb1"
WIDTH = 480
HEIGHT = 320

DB_PATH = "/etc/pihole/pihole-FTL.db"

font = ImageFont.load_default()

def get_stats():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Total queries
        cursor.execute("SELECT COUNT(*) FROM queries;")
        total = cursor.fetchone()[0]

        # Blocked queries (status != 0 usually means blocked)
        cursor.execute("SELECT COUNT(*) FROM queries WHERE status != 0;")
        blocked = cursor.fetchone()[0]

        conn.close()

        percent = (blocked / total * 100) if total > 0 else 0

        return {
            "queries": total,
            "blocked": blocked,
            "percent": round(percent, 2)
        }

    except Exception as e:
        return None

def draw_screen(stats):
    img = Image.new("RGB", (WIDTH, HEIGHT), "black")
    draw = ImageDraw.Draw(img)

    if stats:
        draw.text((20, 20), "Pi-hole Live Stats", fill="white", font=font)

        draw.text((20, 100), f"Queries: {stats['queries']}", fill="green", font=font)
        draw.text((20, 140), f"Blocked: {stats['blocked']}", fill="red", font=font)
        draw.text((20, 180), f"Blocked %: {stats['percent']}%", fill="yellow", font=font)
    else:
        draw.text((20, 120), "DB Read Error", fill="red", font=font)

    return img

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

while True:
    stats = get_stats()
    img = draw_screen(stats)
    write_fb(img)
    time.sleep(5)
