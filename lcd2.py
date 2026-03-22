#!/usr/bin/env python3
import time
import sqlite3
from PIL import Image, ImageDraw, ImageFont

# --- CONFIGURATION ---
FB_DEV = "/dev/fb1"     # Framebuffer device
WIDTH = 480             # LCD width
HEIGHT = 320            # LCD height
DB_PATH = "/etc/pihole/pihole-FTL.db"
UPDATE_INTERVAL = 5     # seconds

# --- FONTS ---
font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
font_stat  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)

# --- UTILITY FUNCTION: center text ---
def draw_text_center(draw, text, y, font, color):
    # Works with modern Pillow
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = (WIDTH - w) // 2
    draw.text((x, y), text, fill=color, font=font)

# --- GET PI-HOLE STATS ---
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

# --- DRAW THE DASHBOARD ---
def draw_screen(stats):
    img = Image.new("RGB", (WIDTH, HEIGHT), "black")
    draw = ImageDraw.Draw(img)

    if stats:
        # Title
        draw_text_center(draw, "Pi-hole Live Stats", 20, font_title, "white")

        # Main stats
        draw_text_center(draw, f"Queries: {stats['queries']}", 100, font_stat, "green")
        draw_text_center(draw, f"Blocked: {stats['blocked']}", 160, font_stat, "red")
        draw_text_center(draw, f"Blocked %: {stats['percent']}%", 220, font_stat, "yellow")

        # Percentage bar
        bar_height = 20
        bar_y = HEIGHT - 40
        bar_width = int(WIDTH * stats['percent'] / 100)
        draw.rectangle((20, bar_y, 20 + bar_width, bar_y + bar_height), fill="yellow")
        draw.rectangle((20, bar_y, WIDTH-20, bar_y + bar_height), outline="white")
    else:
        draw_text_center(draw, "DB Read Error", HEIGHT//2, font_stat, "red")

    return img

# --- WRITE IMAGE TO FRAMEBUFFER ---
def write_fb(img):
    img = img.convert("RGB")
    with open(FB_DEV, "wb") as f:
        for y in range(HEIGHT):
            for x in range(WIDTH):
                r, g, b = img.getpixel((x, y))
                rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                f.write(rgb565.to_bytes(2, byteorder="little"))

# --- MAIN LOOP ---
while True:
    stats = get_stats()
    img = draw_screen(stats)
    write_fb(img)
    time.sleep(UPDATE_INTERVAL)
