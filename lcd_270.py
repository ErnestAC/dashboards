#!/usr/bin/env python3
import time
import sqlite3
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime

# --- CONFIGURATION ---
FB_DEV = "/dev/fb1"     # Framebuffer device
WIDTH = 480             # LCD width (before rotation)
HEIGHT = 320            # LCD height (before rotation)
DB_PATH = "/etc/pihole/pihole-FTL.db"
UPDATE_INTERVAL = 5     # seconds

# --- FONTS ---
font_clock = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 60)
font_stat  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40)

# --- UTILITY FUNCTION: draw left-aligned text ---
def draw_text_left(draw, text, y, font, color, x=20):
    draw.text((x, y), text, fill=color, font=font)

# --- GET PI-HOLE STATS ---
def get_stats():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Read counters table
        cursor.execute("SELECT id, value FROM counters;")
        rows = cursor.fetchall()
        conn.close()

        stats = {"queries": 0, "blocked": 0}
        for id, value in rows:
            if id == 0:
                stats["queries"] = value       # total queries today
            elif id == 1:
                stats["blocked"] = value       # blocked today

        stats["percent"] = (stats["blocked"] / stats["queries"] * 100) if stats["queries"] > 0 else 0
        return stats
    except Exception:
        return None

# --- DRAW THE DASHBOARD ---
def draw_screen(stats):
    # Create image in normal orientation first
    img = Image.new("RGB", (WIDTH, HEIGHT), "black")
    draw = ImageDraw.Draw(img)

    # Clock (HH:MM)
    now = datetime.now().strftime("%H:%M")
    draw_text_left(draw, now, 20, font_clock, "white")

    # Stats start below clock
    y_start = 100
    line_spacing = 60

    if stats:
        draw_text_left(draw, f"Queries: {stats['queries']}", y_start, font_stat, "green")
        draw_text_left(draw, f"Blocked: {stats['blocked']}", y_start + line_spacing, font_stat, "red")
        draw_text_left(draw, f"Blocked %: {stats['percent']:.2f}%", y_start + 2*line_spacing, font_stat, "yellow")

        # Percentage bar at bottom
        bar_height = 20
        bar_y = HEIGHT - 40
        bar_width = int(WIDTH * stats['percent'] / 100)
        draw.rectangle((20, bar_y, 20 + bar_width, bar_y + bar_height), fill="yellow")
        draw.rectangle((20, bar_y, WIDTH-20, bar_y + bar_height), outline="white")
    else:
        draw_text_left(draw, "DB Read Error", HEIGHT//2, font_stat, "red")

    # --- ROTATE 90° CLOCKWISE to correct 270° physical rotation ---
    img = img.rotate(-90, expand=True)

    return img

# --- WRITE IMAGE TO FRAMEBUFFER ---
def write_fb(img):
    img = img.convert("RGB")
    w, h = img.size  # Use rotated image dimensions
    with open(FB_DEV, "wb") as f:
        for y in range(h):
            for x in range(w):
                r, g, b = img.getpixel((x, y))
                rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                f.write(rgb565.to_bytes(2, byteorder="little"))

# --- MAIN LOOP ---
while True:
    stats = get_stats()
    img = draw_screen(stats)
    write_fb(img)
    time.sleep(UPDATE_INTERVAL)
