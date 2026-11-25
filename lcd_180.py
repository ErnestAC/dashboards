#!/usr/bin/env python3
import time, sqlite3, socket, psutil
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime, timedelta
from collections import deque

# --- CONFIG ---
import os
FB_DEV = os.environ.get("LCD_FB_DEV", "/dev/fb1")
WIDTH, HEIGHT = 480, 320
DB_PATH = "/etc/pihole/pihole-FTL.db"
UPDATE_INTERVAL = 4

HEADER_H = 26
FOOTER_H = 20

# bars: same offset from column start and same width everywhere
BAR_DX, BAR_W, BAR_H = 90, 100, 8

# --- FONTS ---
FONT_PATH = "/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf"
font       = ImageFont.truetype(FONT_PATH, 14)
font_small = ImageFont.truetype(FONT_PATH, 12)
font_label = ImageFont.truetype(FONT_PATH, 10)
font_list  = ImageFont.truetype(FONT_PATH, 13)
font_value = ImageFont.truetype(FONT_PATH, 18)
font_title = ImageFont.truetype(FONT_PATH, 16)
font_big   = ImageFont.truetype(FONT_PATH, 46)

# --- HISTORY BUFFER (sparkline) ---
history = deque(maxlen=60)

# --- HELPERS ---
def text(draw,x,y,s,c,f=font): draw.text((x,y),s,fill=c,font=f)

def text_right(draw,x,y,s,c,f):
    w = draw.textbbox((0,0),s,font=f)[2]
    draw.text((x-w,y),s,fill=c,font=f)

def bar(draw,x,y,pct,w,h,color):
    fill=int(w*pct/100)
    draw.rectangle((x,y,x+fill,y+h),fill=color)
    draw.rectangle((x,y,x+w,y+h),outline=color)

def ellipsize(s,n=26): return s if len(s)<=n else s[:n-1]+"…"

# --- SYSTEM ---
def get_ip():
    try:
        s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        s.connect(("8.8.8.8",80))
        ip=s.getsockname()[0]; s.close(); return ip
    except: return "N/A"

def get_hostname(): return socket.gethostname()

def get_temp():
    try:
        return int(open("/sys/class/thermal/thermal_zone0/temp").read())/1000
    except: return 0

def get_cpu(): return psutil.cpu_percent()

def get_mem(): return psutil.virtual_memory().percent

def get_uptime():
    try:
        t=int(float(open("/proc/uptime").read().split()[0]))
        td=timedelta(seconds=t)
        return f"{td.days}d {td.seconds//3600}h {(td.seconds//60)%60}m"
    except: return "N/A"

# --- PIHOLE ---
def get_stats():
    try:
        conn=sqlite3.connect(DB_PATH); c=conn.cursor()

        c.execute("""
        SELECT COUNT(*),
               SUM(CASE WHEN status=1 THEN 1 ELSE 0 END),
               SUM(CASE WHEN type=1 THEN 1 ELSE 0 END),
               MAX(timestamp)
        FROM queries
        WHERE timestamp >= strftime('%s','now','start of day');
        """)
        total,blocked,forwarded,last=c.fetchone()

        c.execute("SELECT COUNT(DISTINCT client) FROM queries WHERE timestamp >= strftime('%s','now','start of day');")
        clients=c.fetchone()[0]

        # Last 7 blocked queries
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
            "total":total or 0,
            "blocked":blocked or 0,
            "forwarded":forwarded or 0,
            "blocked_pct":(blocked/total*100) if total else 0,
            "forwarded_pct":(forwarded/total*100) if total else 0,
            "clients":clients,
            "last":datetime.fromtimestamp(last).strftime("%H:%M") if last else "N/A",
            "last_blocked":last_blocked
        }
    except Exception as e:
        print(e); return None

# --- SPARKLINE ---
def draw_sparkline(draw,x,y,w,h,color):
    if len(history)<2: return
    maxv=max(history) or 1
    step=w/(len(history)-1)

    for i in range(len(history)-1):
        x1=int(x+i*step)
        x2=int(x+(i+1)*step)
        y1=int(y+h-(history[i]/maxv)*h)
        y2=int(y+h-(history[i+1]/maxv)*h)
        draw.line((x1,y1,x2,y2),fill=color)

# --- DRAW ---
# Palette (dark theme, one accent per meaning)
BG     = (8, 10, 14)
PANEL  = (24, 28, 36)
TRACK  = (48, 54, 66)
FG     = (230, 232, 236)
DIM    = (130, 138, 150)
RED    = (239, 83, 80)
BLUE   = (66, 165, 245)
GREEN  = (102, 187, 106)
AMBER  = (255, 183, 77)

M = 10  # outer margin

def text_w(draw, s, f):
    return draw.textbbox((0, 0), s, font=f)[2]

def fit(draw, s, f, maxw):
    """Trim s with an ellipsis so it fits maxw pixels."""
    if text_w(draw, s, f) <= maxw: return s
    while s and text_w(draw, s + "…", f) > maxw: s = s[:-1]
    return s + "…"

def panel(draw, box, fill=PANEL, r=6):
    # rounded_rectangle needs Pillow >= 8.2; fall back to square corners
    if hasattr(draw, "rounded_rectangle"):
        draw.rounded_rectangle(box, radius=r, fill=fill)
    else:
        draw.rectangle(box, fill=fill)

def level(pct, warn, crit):
    return RED if pct >= crit else AMBER if pct >= warn else GREEN

def meter(draw, x, y, w, pct, color, h=4):
    pct = max(0, min(100, pct))
    draw.rectangle((x, y, x + w, y + h), fill=TRACK)
    if pct > 0:
        draw.rectangle((x, y, x + int(w * pct / 100), y + h), fill=color)

def tile(draw, x, y, w, h, label, value, pct=None, color=FG):
    panel(draw, (x, y, x + w, y + h))
    text(draw, x + 8, y + 5, label, DIM, font_label)
    text(draw, x + 8, y + 17, fit(draw, value, font_value, w - 16),
         color if pct is not None else FG, font_value)
    if pct is not None:
        meter(draw, x + 8, y + h - 10, w - 16, pct, color)

def get_uptime_short():
    """'12d 7h' when up for days, otherwise '7h 43m' (fits the tile)."""
    try:
        t = int(float(open("/proc/uptime").read().split()[0]))
        d, h, m = t // 86400, (t // 3600) % 24, (t // 60) % 60
        return f"{d}d {h}h" if d else f"{h}h {m}m"
    except: return "N/A"

def draw_screen(stats):
    cpu = get_cpu()
    temp = get_temp()
    mem = get_mem()
    ALERT = cpu > 80 or temp > 70

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # HEADER: hostname left, today's queries right; turns red on alert
    hdr = RED if ALERT else PANEL
    draw.rectangle((0, 0, WIDTH, HEADER_H), fill=hdr)
    if not ALERT:
        draw.rectangle((0, HEADER_H - 2, WIDTH, HEADER_H), fill=RED)
    text(draw, M, 3, get_hostname(), FG, font_title)
    if stats:
        n = f"{stats['total']:,}"
        unit = " queries"
        uw = text_w(draw, unit, font)
        text_right(draw, WIDTH - M, 5, unit, FG if ALERT else DIM, font)
        text_right(draw, WIDTH - M - uw, 5, n, FG, font)
    else:
        text_right(draw, WIDTH - M, 5, "-- queries", DIM, font)

    # SYSTEM TILES
    ty, th, gap = HEADER_H + 8, 52, 8
    tw = (WIDTH - 2 * M - 3 * gap) // 4
    xs = [M + i * (tw + gap) for i in range(4)]
    tile(draw, xs[0], ty, tw, th, "CPU", f"{cpu:.0f}%", cpu, level(cpu, 60, 80))
    tile(draw, xs[1], ty, tw, th, "MEMORY", f"{mem:.0f}%", mem, level(mem, 70, 90))
    tile(draw, xs[2], ty, tw, th, "TEMP", f"{temp:.1f}°C",
         temp / 85 * 100, level(temp, 60, 70))
    tile(draw, xs[3], ty, tw, th, "UPTIME", get_uptime_short())

    top = ty + th + 8
    bottom = HEIGHT - FOOTER_H - 8

    if stats:
        history.append(stats["total"])

        # LEFT: latest blocked domains
        lw = 270
        panel(draw, (M, top, M + lw, bottom))
        text(draw, M + 10, top + 7, "LATEST BLOCKED", DIM, font_label)
        y = top + 24
        for d in stats["last_blocked"][:7]:
            draw.ellipse((M + 11, y + 6, M + 16, y + 11), fill=RED)
            text(draw, M + 24, y, fit(draw, d, font_list, lw - 34), FG, font_list)
            y += 24

        # RIGHT: blocked share + key numbers
        rx0 = M + lw + gap
        rx1 = WIDTH - M
        rw = rx1 - rx0
        panel(draw, (rx0, top, rx1, bottom))
        text(draw, rx0 + 10, top + 7, "BLOCKED TODAY", DIM, font_label)
        pct = f"{stats['blocked_pct']:.1f}%"
        text(draw, rx0 + 10, top + 16, pct, RED, font_big)

        # stacked share bar: blocked / forwarded / other (cached etc.)
        bx, by, bw, bh = rx0 + 10, top + 76, rw - 20, 6
        draw.rectangle((bx, by, bx + bw, by + bh), fill=TRACK)
        b = int(bw * stats["blocked_pct"] / 100)
        f = int(bw * stats["forwarded_pct"] / 100)
        if b: draw.rectangle((bx, by, bx + b, by + bh), fill=RED)
        if f: draw.rectangle((bx + b, by, min(bx + b + f, bx + bw), by + bh), fill=BLUE)

        rows = [
            ("Blocked",    f"{stats['blocked']:,}",           RED),
            ("Forwarded",  f"{stats['forwarded_pct']:.0f}%",  BLUE),
            ("Clients",    f"{stats['clients']}",             FG),
            ("Last query", stats["last"],                     FG),
        ]
        y = by + 16
        for label, val, c in rows:
            text(draw, rx0 + 10, y, label, DIM, font_list)
            text_right(draw, rx1 - 10, y, val, c, font_list)
            y += 23
    else:
        panel(draw, (M, top, WIDTH - M, bottom))
        msg = "Pi-hole database unavailable"
        text(draw, (WIDTH - text_w(draw, msg, font)) // 2,
             (top + bottom) // 2 - 8, msg, DIM, font)

    # FOOTER: IP left, date/time right
    fy = HEIGHT - FOOTER_H
    draw.rectangle((0, fy, WIDTH, HEIGHT), fill=PANEL)
    text(draw, M, fy + 3, "IP", DIM, font_small)
    text(draw, M + 18, fy + 3, get_ip(), FG, font_small)
    text_right(draw, WIDTH - M, fy + 3,
               datetime.now().strftime("%a %d %b  %H:%M"), FG, font_small)

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

# --- LOOP ---
if __name__=="__main__":
    while True:
        write_fb(draw_screen(get_stats()))
        time.sleep(UPDATE_INTERVAL)
