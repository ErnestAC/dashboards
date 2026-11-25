#!/usr/bin/env python3
import time
import sqlite3
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime, timedelta

# --- CONFIGURATION ---
import os
FB_DEV = os.environ.get("LCD_FB_DEV", "/dev/fb1")        # Framebuffer device
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

def draw_bar(draw, y, percent, color=(255, 255, 255), height=12, margin=10):
    bar_width = int((WIDTH - 2*margin) * percent / 100)
    draw.rectangle((margin, y, margin + bar_width, y + height), fill=color)
    draw.rectangle((margin, y, WIDTH - margin, y + height), outline=(255, 255, 255))

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
    draw_text_left(draw, f"Uptime: {uptime}", y, font_large, (255, 255, 255))
    y += 30

    if stats:
        draw_text_left(draw, f"Total: {stats['queries']} | Allowed: {stats['allowed']}", y, font_medium, (200, 200, 200))
        y += 20
        draw_text_left(draw, f"Blocked: {stats['blocked']} ({stats['blocked_percent']:.1f}%)", y, font_medium, (180, 180, 180))
        draw_bar(draw, y + 18, stats['blocked_percent'], color=(180, 180, 180))
        y += 30
        draw_text_left(draw, f"Forwarded: {stats['forwarded']} ({stats['forwarded_percent']:.1f}%)", y, font_medium, (150, 150, 150))
        draw_bar(draw, y + 18, stats['forwarded_percent'], color=(150, 150, 150))
        y += 30
        draw_text_left(draw, f"Clients: {stats['clients']} | Avg/Client: {stats['avg_per_client']:.1f}", y, font_medium, (150, 150, 150))
        y += 20
        draw_text_left(draw, f"Last query: {stats['last_query']}", y, font_medium, (150, 150, 150))
        y += 20
        draw_text_left(draw, "Top Domains:", y, font_medium, (150, 150, 150))
        y += 18
        for domain, count in stats["top_domains"]:
            draw_text_left(draw, f"{domain}: {count}", y, font_small, (120, 120, 120), x=20)
            y += 16
    else:
        draw_text_left(draw, "DB Read Error", HEIGHT//2, font_medium, (255, 255, 255))

    return img

# --- WRITE TO FRAMEBUFFER ---
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
if __name__ == "__main__":
    while True:
        stats = get_stats()
        img = draw_screen(stats)
        write_fb(img)
        time.sleep(UPDATE_INTERVAL)
