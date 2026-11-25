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

HEADER_H = 24
FOOTER_H = 20

# --- FONTS ---
font = ImageFont.truetype("/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf", 14)
font_small = ImageFont.truetype("/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf", 12)
font_big = ImageFont.truetype("/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf", 82)

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
def draw_screen(stats):
    cpu=get_cpu()
    temp=get_temp()

    ALERT = cpu>80 or temp>70

    BG=(255,255,255) if ALERT else (0,0,0)
    FG=(0,0,0) if ALERT else (200,200,200)
    DIM=(180,180,180) if not ALERT else (80,80,80)

    img=Image.new("RGB",(WIDTH,HEIGHT),BG)
    draw=ImageDraw.Draw(img)

    # HEADER
    draw.rectangle((0,0,WIDTH,HEADER_H),fill=FG)
    text(draw,10,2,get_hostname(),BG)

    #pct=f"{stats['blocked_pct']:.0f}%" if stats else "0%"
    #text_right(draw,WIDTH-10,2,pct,BG,font)

    draw.line((0,HEADER_H,WIDTH,HEADER_H),fill=DIM)

    y=HEADER_H+5
    lx,rx=10,250

    # SYSTEM GRID
    text(draw,lx,y,f"IP: {get_ip()}",FG)
    text(draw,rx,y,f"CPU: {cpu:.0f}%",FG)
    bar(draw,rx+90,y+4,cpu,100,8,FG)

    y+=20
    mem=get_mem()
    text(draw,lx,y,f"MEM: {mem:.0f}%",FG)
    bar(draw,lx+90,y+4,mem,100,8,FG)
    text(draw,rx,y,f"TEMP: {temp:.1f}C",FG)

    y+=25
    text(draw,lx,y,f"Uptime: {get_uptime()}",FG)

    if stats:
        # update history
        history.append(stats["total"])

        y+=25
        text(draw,lx,y,f"Blk: {stats['blocked_pct']:.0f}%",FG)
        bar(draw,lx+120,y+4,stats['blocked_pct'],100,8,FG)

        text(draw,rx,y,f"Fwd: {stats['forwarded_pct']:.0f}%",FG)
        bar(draw,rx+80,y+4,stats['forwarded_pct'],80,8,FG)

        y+=20
        text(draw,lx,y,f"Total: {stats['total']}",FG)
        text(draw,rx,y,f"Clients: {stats['clients']}",FG)

        y+=20
        text(draw,lx,y,f"Last: {stats['last']}",FG)

        # divider
        y+=20
        draw.line((10,y,WIDTH-10,y),fill=DIM)

        # last 7 blocked instead of top domains
        y+=10
        text(draw,lx,y,"Latest Blocked Domains",FG)

        # BIG %
        bx,by=300,y+2
        text(draw,bx,by,f"{stats['blocked_pct']:.0f}%",FG,font_big)
        text(draw,bx+6,by+82,"blocked",DIM,font_small)

        # blocked domains
        y+=18
        for d in stats["last_blocked"]:
            text(draw,lx+10,y,f"{ellipsize(d)}",FG,font_small)
            y+=14

        # SPARKLINE
        #draw_sparkline(draw,250,HEIGHT-80,200,40,FG)

    # FOOTER
    draw.rectangle((0,HEIGHT-FOOTER_H,WIDTH,HEIGHT),fill=FG)
    draw.line((0,HEIGHT-FOOTER_H,WIDTH,HEIGHT-FOOTER_H),fill=DIM)

    text_right(draw,WIDTH-10,HEIGHT-FOOTER_H+2,str(int(time.time())),BG,font)

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
