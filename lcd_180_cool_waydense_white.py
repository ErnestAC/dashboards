#!/usr/bin/env python3
import time
import sqlite3
import socket
import psutil
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime, timedelta

# --- CONFIG ---
FB_DEV = "/dev/fb1"
WIDTH, HEIGHT = 480, 320
DB_PATH = "/etc/pihole/pihole-FTL.db"
UPDATE_INTERVAL = 5

HEADER_H = 20
FOOTER_H = 20

# --- FONTS ---
font = ImageFont.truetype("/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf", 14)
font_small = ImageFont.truetype("/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf", 12)
font_big = ImageFont.truetype("/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf", 42)
font_label = ImageFont.truetype("/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf", 12)

# --- HELPERS ---
def text(draw, x, y, s, color=(0,0,0), f=font):
    draw.text((x, y), s, fill=color, font=f)

def text_right(draw, x, y, s, color, f):
    bbox = draw.textbbox((0,0), s, font=f)
    w = bbox[2] - bbox[0]
    draw.text((x - w, y), s, fill=color, font=f)

def bar(draw, x, y, pct, w=120, h=10):
    fill = int(w * pct / 100)
    draw.rectangle((x, y, x+fill, y+h), fill=(0,0,0))
    draw.rectangle((x, y, x+w, y+h), outline=(0,0,0))

def ellipsize(s, max_chars=26):
    return s if len(s) <= max_chars else s[:max_chars-1] + "…"

# --- SYSTEM ---
def get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "N/A"

def get_hostname():
    return socket.gethostname()

def get_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return f"{int(f.read())/1000:.1f}C"
    except:
        return "N/A"

def get_cpu():
    return psutil.cpu_percent()

def get_mem():
    return psutil.virtual_memory().percent

def get_uptime():
    try:
        with open("/proc/uptime") as f:
            t = int(float(f.readline().split()[0]))
            td = timedelta(seconds=t)
            return f"{td.days}d {td.seconds//3600}h {(td.seconds//60)%60}m"
    except:
        return "N/A"

# --- PIHOLE ---
def get_stats():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

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
            SELECT domain, COUNT(*) cnt
            FROM queries
            WHERE timestamp >= strftime('%s','now','start of day')
            GROUP BY domain
            ORDER BY cnt DESC LIMIT 5;
        """)
        top = c.fetchall()

        conn.close()

        return {
            "total": total,
            "blocked": blocked,
            "forwarded": forwarded,
            "blocked_pct": (blocked/total*100) if total else 0,
            "forwarded_pct": (forwarded/total*100) if total else 0,
            "clients": clients,
            "last": datetime.fromtimestamp(last).strftime("%H:%M") if last else "N/A",
            "top": top
        }
    except Exception as e:
        print(e)
        return None

# --- DRAW ---
def draw_screen(stats):
    img = Image.new("RGB", (WIDTH, HEIGHT), (200,200,200))
    draw = ImageDraw.Draw(img)

    # --- HEADER ---
    draw.rectangle((0, 0, WIDTH, HEADER_H), fill=(0,0,0))
    draw.line((0, HEADER_H, WIDTH, HEADER_H), fill=(100,100,100))

    hostname = get_hostname()
    pct = f"{stats['blocked_pct']:.0f}%" if stats else "0%"

    text(draw, 10, 2, hostname, (255,255,255))
    text_right(draw, WIDTH-10, 2, pct, (255,255,255), font)

    # --- CONTENT ---
    y = HEADER_H + 5
    left_x = 10
    right_x = 250

    text(draw, left_x, y, f"IP: {get_ip()}")
    cpu = get_cpu()
    text(draw, right_x, y, f"CPU: {cpu:.0f}%")
    bar(draw, right_x+90, y+4, cpu)

    y += 20
    mem = get_mem()
    text(draw, left_x, y, f"MEM: {mem:.0f}%")
    bar(draw, left_x+90, y+4, mem)
    text(draw, right_x, y, f"TEMP: {get_temp()}")

    y += 25
    text(draw, left_x, y, f"Uptime: {get_uptime()}")

    if stats:
        y += 25

        text(draw, left_x, y, f"Blocked: {stats['blocked_pct']:.0f}%")
        bar(draw, left_x+120, y+4, stats['blocked_pct'])

        text(draw, right_x, y, f"Fwd: {stats['forwarded_pct']:.0f}%")
        bar(draw, right_x+80, y+4, stats['forwarded_pct'])

        y += 20
        text(draw, left_x, y, f"Total: {stats['total']}")
        text(draw, right_x, y, f"Clients: {stats['clients']}")

        y += 20
        text(draw, left_x, y, f"Last: {stats['last']}")

        # Divider
        y += 20
        draw.line((10, y, WIDTH-10, y), fill=(150,150,150))

        # --- TOP DOMAINS ---
        y += 10
        text(draw, left_x, y, "Top Domains:")

        # BIG BLOCK %
        big_x = 360
        big_y = y + 10

        text(draw, big_x, big_y, f"{stats['blocked_pct']:.0f}%", (0,0,0), font_big)
        text(draw, big_x+10, big_y+45, "blocked", (120,120,120), font_label)

        y += 20
        for d, c in stats["top"]:
            text(draw, left_x+10, y, f"{ellipsize(d)}  {c}", (0,0,0), font_small)
            y += 14

    # --- FOOTER ---
    draw.rectangle((0, HEIGHT-FOOTER_H, WIDTH, HEIGHT), fill=(0,0,0))
    draw.line((0, HEIGHT-FOOTER_H, WIDTH, HEIGHT-FOOTER_H), fill=(100,100,100))

    unix_time = str(int(time.time()))
    text_right(draw, WIDTH-10, HEIGHT-FOOTER_H+2, unix_time, (255,255,255), font)

    return img

# --- FB ---
def write_fb(img):
    img = img.convert("RGB")
    with open(FB_DEV, "wb") as f:
        for y in range(HEIGHT):
            for x in range(WIDTH):
                r,g,b = img.getpixel((x,y))
                rgb565 = ((r&0xF8)<<8)|((g&0xFC)<<3)|(b>>3)
                f.write(rgb565.to_bytes(2,"little"))

# --- LOOP ---
if __name__ == "__main__":
    while True:
        write_fb(draw_screen(get_stats()))
        time.sleep(UPDATE_INTERVAL)
