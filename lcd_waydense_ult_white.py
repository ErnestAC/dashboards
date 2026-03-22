#!/usr/bin/env python3
import time, sqlite3, socket, psutil
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime, timedelta
from collections import deque

# --- CONFIG ---
FB_DEV = "/dev/fb1"
WIDTH, HEIGHT = 480, 320
DB_PATH = "/etc/pihole/pihole-FTL.db"
UPDATE_INTERVAL = 2

HEADER_H = 20
FOOTER_H = 20

# --- FONTS ---
font = ImageFont.truetype("/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf", 14)
font_small = ImageFont.truetype("/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf", 12)
font_big = ImageFont.truetype("/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf", 42)
font_label = ImageFont.truetype("/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf", 10)

# --- HISTORY BUFFERS ---
history_queries = deque(maxlen=60)
history_qps = deque(maxlen=60)

# --- HELPERS ---
def text(draw,x,y,s,c,f=font): draw.text((x,y),s,fill=c,font=f)
def text_right(draw,x,y,s,c,f): draw.text((x-draw.textbbox((0,0),s,font=f)[2],y),s,fill=c,font=f)
def bar(draw,x,y,pct,w,h,color): draw.rectangle((x,y,x+int(w*pct/100),y+h),fill=color); draw.rectangle((x,y,x+w,y+h),outline=color)
def ellipsize(s,n=26): return s if len(s)<=n else s[:n-1]+"…"

# --- SYSTEM ---
def get_ip():
    try: s=socket.socket(); s.connect(("8.8.8.8",80)); ip=s.getsockname()[0]; s.close(); return ip
    except: return "N/A"
def get_hostname(): return socket.gethostname()
def get_temp():
    try: return int(open("/sys/class/thermal/thermal_zone0/temp").read())/1000
    except: return 0
def get_cpu(): return psutil.cpu_percent()
def get_mem(): return psutil.virtual_memory().percent
def get_uptime():
    try: t=int(float(open("/proc/uptime").read().split()[0])); td=timedelta(seconds=t); return f"{td.days}d {td.seconds//3600}h {(td.seconds//60)%60}m"
    except: return "N/A"

# --- PIHOLE ---
def get_stats():
    try:
        conn=sqlite3.connect(DB_PATH); c=conn.cursor()

        # Basic stats
        c.execute("""
        SELECT COUNT(*),SUM(CASE WHEN status=1 THEN 1 ELSE 0 END),SUM(CASE WHEN type=1 THEN 1 ELSE 0 END),MAX(timestamp),AVG(reply_time)
        FROM queries WHERE timestamp >= strftime('%s','now','start of day');
        """)
        total,blocked,forwarded,last,avg_latency=c.fetchone()
        avg_latency=avg_latency or 0

        c.execute("SELECT COUNT(DISTINCT client) FROM queries WHERE timestamp >= strftime('%s','now','start of day');")
        clients=c.fetchone()[0]

        # Top domains
        c.execute("SELECT domain,COUNT(*) FROM queries WHERE timestamp >= strftime('%s','now','start of day') GROUP BY domain ORDER BY COUNT(*) DESC LIMIT 5;")
        top=c.fetchall()

        # Top clients
        c.execute("SELECT client,COUNT(*) FROM queries WHERE timestamp >= strftime('%s','now','start of day') GROUP BY client ORDER BY COUNT(*) DESC LIMIT 3;")
        top_clients=c.fetchall()

        conn.close()
        return {
            "total":total or 0,
            "blocked":blocked or 0,
            "forwarded":forwarded or 0,
            "blocked_pct":(blocked/total*100) if total else 0,
            "forwarded_pct":(forwarded/total*100) if total else 0,
            "clients":clients,
            "last":datetime.fromtimestamp(last).strftime("%H:%M") if last else "N/A",
            "top":top,
            "top_clients":top_clients,
            "latency":avg_latency
        }
    except Exception as e:
        print(e); return None

# --- SPARKLINE ---
def draw_sparkline(draw,x,y,w,h,data,color):
    if len(data)<2: return
    maxv=max(data) or 1
    step=w/(len(data)-1)
    for i in range(len(data)-1):
        x1=int(x+i*step); x2=int(x+(i+1)*step)
        y1=int(y+h-(data[i]/maxv)*h); y2=int(y+h-(data[i+1]/maxv)*h)
        draw.line((x1,y1,x2,y2),fill=color)

# --- DRAW ---
def draw_screen(stats):
    cpu=get_cpu(); temp=get_temp()
    ALERT = cpu>80 or temp>70

    BG=(0,0,0) if ALERT else (200,200,200)
    FG=(255,255,255) if ALERT else (0,0,0)
    DIM=(180,180,180) if not ALERT else (80,80,80)

    img=Image.new("RGB",(WIDTH,HEIGHT),BG)
    draw=ImageDraw.Draw(img)

    # HEADER
    draw.rectangle((0,0,WIDTH,HEADER_H),fill=FG)
    text(draw,10,2,get_hostname(),BG)
    pct=f"{stats['blocked_pct']:.0f}%" if stats else "0%"
    text_right(draw,WIDTH-10,2,pct,BG,font)
    draw.line((0,HEADER_H,WIDTH,HEADER_H),fill=DIM)

    y=HEADER_H+5; lx,rx=10,250

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
        history_queries.append(stats["total"])
        history_qps.append(stats["total"]-(history_queries[-2] if len(history_queries)>1 else stats["total"]))
        y+=25

        # INLINE BARS
        text(draw,lx,y,f"Blocked: {stats['blocked_pct']:.0f}%",FG)
        bar(draw,lx+120,y+4,stats['blocked_pct'],100,8,FG)
        text(draw,rx,y,f"Fwd: {stats['forwarded_pct']:.0f}%",FG)
        bar(draw,rx+80,y+4,stats['forwarded_pct'],80,8,FG)
        y+=20
        text(draw,lx,y,f"Total: {stats['total']}",FG)
        text(draw,rx,y,f"Clients: {stats['clients']}",FG)
        y+=20
        text(draw,lx,y,f"Last: {stats['last']}",FG)
        text(draw,rx,y,f"Avg Lat: {stats['latency']:.1f}ms",FG)
        y+=20

        # Divider
        draw.line((10,y,WIDTH-10,y),fill=DIM); y+=10

        # TOP DOMAINS + BIG %
        text(draw,lx,y,"Top Domains:",FG)
        bx,by=340,y+5
        text(draw,bx,by,f"{stats['blocked_pct']:.0f}%",FG,font_big)
        text(draw,bx+10,by+45,"blocked",DIM,font_label)
        y+=18
        for d,cnt in stats["top"]:
            text(draw,lx+10,y,f"{ellipsize(d)} {cnt}",FG,font_small)
            y+=14

        # TOP CLIENTS
        y+=2
        text(draw,rx,y,"Top Clients:",FG)
        cy=y+14
        for c,ct in stats["top_clients"]:
            text(draw,rx+10,cy,f"{c} {ct}",FG,font_small)
            cy+=12

        # SPARKLINE
        draw_sparkline(draw,250,HEIGHT-80,200,40,history_qps,FG)

    # FOOTER
    draw.rectangle((0,HEIGHT-FOOTER_H,WIDTH,HEIGHT),fill=FG)
    draw.line((0,HEIGHT-FOOTER_H,WIDTH,HEIGHT-FOOTER_H),fill=DIM)
    text_right(draw,WIDTH-10,HEIGHT-FOOTER_H+2,str(int(time.time())),BG,font)

    return img

# --- FB ---
def write_fb(img):
    img=img.convert("RGB")
    with open(FB_DEV,"wb") as f:
        for y in range(HEIGHT):
            for x in range(WIDTH):
                r,g,b=img.getpixel((x,y))
                rgb565=((r&0xF8)<<8)|((g&0xFC)<<3)|(b>>3)
                f.write(rgb565.to_bytes(2,"little"))

# --- LOOP ---
if __name__=="__main__":
    while True:
        write_fb(draw_screen(get_stats()))
        time.sleep(UPDATE_INTERVAL)
