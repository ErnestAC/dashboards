#!/usr/bin/env python3
import time
import sqlite3
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime

# --- CONFIGURATION ---
import os
FB_DEV = os.environ.get("LCD_FB_DEV", "/dev/fb1")       # Framebuffer device
WIDTH = 320               # Portrait width
HEIGHT = 480              # Portrait height
DB_PATH = "/etc/pihole/pihole-FTL.db"
UPDATE_INTERVAL = 5       # seconds

# --- FONTS ---
font_clock = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 80)  # Large clock
font_stat  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 50)  # Stats smaller

# --- UTILITY FUNCTION ---
def draw_text_left(draw, text, y, font, color, x=20):
    draw.text((x, y), text, fill=color, font=font)

# --- GET PI-HOLE STATS ---
def get_stats():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, value FROM counters;")
        rows = cursor.fetchall()
        conn.close()

        stats = {"queries": 0, "blocked": 0}
        for id, value in rows:
            if id == 0:
                stats["queries"] = value
            elif id == 1:
                stats["blocked"] = value

        stats["percent"] = (stats["blocked"] / stats["queries"] * 100) if stats["queries"] > 0 else 0
        return stats
    except Exception:
        return None

# --- DRAW DASHBOARD ---
def draw_screen(stats):
    img = Image.new("RGB", (WIDTH, HEIGHT), "black")
    draw = ImageDraw.Draw(img)

    # Clock (HH:MM) at top
    now = datetime.now().strftime("%H:%M")
    draw_text_left(draw, now, 20, font_clock, "white")

    # Stats below clock
    y_start = 150
    line_spacing = 70

    if stats:
        draw_text_left(draw, f"Queries: {stats['queries']}", y_start, font_stat, "green")
        draw_text_left(draw, f"Blocked: {stats['blocked']}", y_start + line_spacing, font_stat, "red")
        draw_text_left(draw, f"Blocked %: {stats['percent']:.2f}%", y_start + 2*line_spacing, font_stat, "yellow")

        # Percentage bar at bottom
        bar_height = 40
        bar_y = HEIGHT - 60
        bar_width = int((WIDTH - 40) * stats['percent'] / 100)
        draw.rectangle((20, bar_y, 20 + bar_width, bar_y + bar_height), fill="yellow")
        draw.rectangle((20, bar_y, WIDTH-20, bar_y + bar_height), outline="white", width=2)
    else:
        draw_text_left(draw, "DB Read Error", HEIGHT//2, font_stat, "red")

    # Rotate 90° clockwise to correct for 270° physical rotation
    img = img.rotate(90)

    return img

# --- WRITE IMAGE TO FRAMEBUFFER ---
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

# --- MAIN LOOP ---
while True:
    stats = get_stats()
    img = draw_screen(stats)
    write_fb(img)
    time.sleep(UPDATE_INTERVAL)
