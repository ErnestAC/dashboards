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

def write_fb(img):
    img = img.convert("RGB")

    with open(FB_DEV, "wb") as f:
        for y in range(HEIGHT):
            for x in range(WIDTH):
                r, g, b = img.getpixel((x, y))
                rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                f.write(rgb565.to_bytes(2, byteorder="little"))

while True:
    stats = get_stats()
    img = draw_screen(stats)
    write_fb(img)
    time.sleep(5)
