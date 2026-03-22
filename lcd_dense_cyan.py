#!/usr/bin/env python3
import time
import sqlite3
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime, timedelta

# --- CONFIGURATION ---
FB_DEV = "/dev/fb1"        # Framebuffer device
WIDTH = 480
HEIGHT = 320
DB_PATH = "/etc/pihole/pihole-FTL.db"
UPDATE_INTERVAL = 5        # seconds

# --- FONTS (Roboto) ---
font_small  = ImageFont.truetype("/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf", 12)
font_medium = ImageFont.truetype("/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf", 16)
font_large  = ImageFont.truetype("/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf", 24)

# --- UTILITY FUNCTIONS ---
def draw_text_left(draw, text, y, font, color, x=10):
    draw.text((x, y), text, fill=color, font=font)

def draw_bar(draw, y, percent, color=(0, 255, 255), height=12, margin=10):
    bar_width = int((WIDTH - 2*margin) * percent / 100)
    draw.rectangle((margin, y, margin + bar_width, y + height), fill=color)
    draw.rectangle((margin, y, WIDTH - margin, y + height), outline=(0, 255, 255))

# --- GET SYSTEM UPTIME ---
def get_uptime():
    try:
        with open("/proc/uptime", "r") as f:
            uptime_sec = float(f.readline().split()[0])
            td = timedelta(seconds=int(uptime_sec))
            days = td.days
            hours, remainder = divmod(td.seconds, 3600)
            minutes = remainder // 60
            return f"{days}d {hours}h {minutes}m"
    except:
        return "N/A"

# --- GET PI-HOLE STATS ---
def get_stats():
    stats = {}
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Total, blocked, forwarded, last query
        cursor.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 1 THEN 1 ELSE 0 END) AS blocked,
                SUM(CASE WHEN type = 1 THEN 1 ELSE 0 END) AS forwarded,
                MAX(timestamp) AS last_query
            FROM queries
            WHERE timestamp >= strftime('%s', 'now', 'start of day');
        """)
        row = cursor.fetchone()
        total, blocked, forwarded, last_query = row
        stats["queries"] = total
        stats["blocked"] = blocked
        stats["forwarded"] = forwarded
        stats["allowed"] = total - blocked
        stats["blocked_percent"] = (blocked / total * 100) if total > 0 else 0
        stats["forwarded_percent"] = (forwarded / total * 100) if total > 0 else 0
        stats["last_query"] = datetime.fromtimestamp(last_query).strftime("%H:%M") if last_query else "N/A"

        # Unique clients
        cursor.execute("""
            SELECT COUNT(DISTINCT client) FROM queries
            WHERE timestamp >= strftime('%s', 'now', 'start of day');
        """)
        stats["clients"] = cursor.fetchone()[0]
        stats["avg_per_client"] = stats["queries"] / stats["clients"] if stats["clients"] > 0 else 0

        # Top 5 domains
        cursor.execute("""
            SELECT domain, COUNT(*) as cnt
            FROM queries
            WHERE timestamp >= strftime('%s', 'now', 'start of day')
            GROUP BY domain
            ORDER BY cnt DESC
            LIMIT 5;
        """)
        stats["top_domains"] = cursor.fetchall()

        conn.close()
        return stats
    except Exception as e:
        print("Error reading DB:", e)
        return None

# --- DRAW DASHBOARD ---
def draw_screen(stats):
    img = Image.new("RGB", (WIDTH, HEIGHT), "black")
    draw = ImageDraw.Draw(img)
    y = 5

    # Uptime
    uptime = get_uptime()
    draw_text_left(draw, f"Uptime: {uptime}", y, font_large, (0, 255, 255))
    y += 30

    if stats:
        draw_text_left(draw, f"Total: {stats['queries']} | Allowed: {stats['allowed']}", y, font_medium, (0, 200, 255))
        y += 20
        draw_text_left(draw, f"Blocked: {stats['blocked']} ({stats['blocked_percent']:.1f}%)", y, font_medium, (0, 180, 255))
        draw_bar(draw, y + 18, stats['blocked_percent'], color=(0, 180, 255))
        y += 30
        draw_text_left(draw, f"Forwarded: {stats['forwarded']} ({stats['forwarded_percent']:.1f}%)", y, font_medium, (0, 160, 255))
        draw_bar(draw, y + 18, stats['forwarded_percent'], color=(0, 160, 255))
        y += 30
        draw_text_left(draw, f"Clients: {stats['clients']} | Avg/Client: {stats['avg_per_client']:.1f}", y, font_medium, (0, 140, 255))
        y += 20
        draw_text_left(draw, f"Last query: {stats['last_query']}", y, font_medium, (0, 120, 255))
        y += 20
        draw_text_left(draw, "Top Domains:", y, font_medium, (0, 100, 255))
        y += 18
        for domain, count in stats["top_domains"]:
            draw_text_left(draw, f"{domain}: {count}", y, font_small, (0, 80, 255), x=20)
            y += 16
    else:
        draw_text_left(draw, "DB Read Error", HEIGHT//2, font_medium, (0, 255, 255))

    return img

# --- WRITE TO FRAMEBUFFER ---
def write_fb(img):
    img = img.convert("RGB")
    with open(FB_DEV, "wb") as f:
        for y in range(HEIGHT):
            for x in range(WIDTH):
                r, g, b = img.getpixel((x, y))
                rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                f.write(rgb565.to_bytes(2, byteorder="little"))

# --- MAIN LOOP ---
if __name__ == "__main__":
    while True:
        stats = get_stats()
        img = draw_screen(stats)
        write_fb(img)
        time.sleep(UPDATE_INTERVAL)
