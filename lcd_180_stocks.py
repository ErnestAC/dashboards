#!/usr/bin/env python3
"""Pi-hole LCD (480x320): stock ticker. The block rate traded as $PIHL: hourly candlesticks over 24 h, volume bars, ticker strip."""
import time, sqlite3, socket, psutil
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime

# --- CONFIG ---
FB_DEV = "/dev/fb1"
WIDTH, HEIGHT = 480, 320
DB_PATH = "/etc/pihole/pihole-FTL.db"
UPDATE_INTERVAL = 4

# --- SYSTEM ---
def get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]; s.close(); return ip
    except: return "N/A"

def get_hostname(): return socket.gethostname()

def get_temp():
    try: return int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000
    except: return 0

def get_cpu(): return psutil.cpu_percent()

def get_mem(): return psutil.virtual_memory().percent

def get_uptime_short():
    """'12d 7h' when up for days, otherwise '7h 43m'."""
    try:
        t = int(float(open("/proc/uptime").read().split()[0]))
        d, h, m = t // 86400, (t // 3600) % 24, (t // 60) % 60
        return f"{d}d {h}h" if d else f"{h}h {m}m"
    except: return "N/A"

# --- PIHOLE ---
def get_stats():
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
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
        SELECT domain FROM queries
        WHERE status=1 AND timestamp >= strftime('%s','now','-1 day')
        GROUP BY domain
        ORDER BY MAX(timestamp) DESC
        LIMIT 7;
        """)
        last_blocked = [row[0] for row in c.fetchall()]
        conn.close()

        return {
            "total": total or 0,
            "blocked": blocked or 0,
            "forwarded": forwarded or 0,
            "blocked_pct": (blocked / total * 100) if total else 0,
            "forwarded_pct": (forwarded / total * 100) if total else 0,
            "clients": clients,
            "last": datetime.fromtimestamp(last).strftime("%H:%M") if last else "N/A",
            "last_blocked": last_blocked,
        }
    except Exception as e:
        print(e); return None

# --- HELPERS ---
def text(draw, x, y, s, c, f): draw.text((x, y), s, fill=c, font=f)

def text_w(draw, s, f): return draw.textbbox((0, 0), s, font=f)[2]

def text_right(draw, x, y, s, c, f): draw.text((x - text_w(draw, s, f), y), s, fill=c, font=f)

def text_center(draw, cx, y, s, c, f): draw.text((cx - text_w(draw, s, f) // 2, y), s, fill=c, font=f)

def fit(draw, s, f, maxw):
    """Trim s with an ellipsis so it fits maxw pixels."""
    if text_w(draw, s, f) <= maxw: return s
    while s and text_w(draw, s + "…", f) > maxw: s = s[:-1]
    return s + "…"

def panel(draw, box, fill, r=6):
    # rounded_rectangle needs Pillow >= 8.2; fall back to square corners
    if hasattr(draw, "rounded_rectangle"): draw.rounded_rectangle(box, radius=r, fill=fill)
    else: draw.rectangle(box, fill=fill)

# --- FONTS ---
FONT_PATH = "/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf"
font_label = ImageFont.truetype(FONT_PATH, 10)
font_small = ImageFont.truetype(FONT_PATH, 12)
font       = ImageFont.truetype(FONT_PATH, 14)
font_title = ImageFont.truetype(FONT_PATH, 22)
font_price = ImageFont.truetype(FONT_PATH, 34)

# --- 24 H MARKET DATA ---
CANDLES = 24
MARKET_REFRESH = 300            # seconds; hourly candles change slowly

def get_market():
    """24 hourly candles of the blocked share, each from four 15-minute buckets:
    [(open, high, low, close, volume) or None], oldest first."""
    now = int(time.time())
    end = now - now % 3600 + 3600              # end of the current hour
    start = end - CANDLES * 3600
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("""
        SELECT (timestamp - ?) / 900, COUNT(*), SUM(CASE WHEN status=1 THEN 1 ELSE 0 END)
        FROM queries WHERE timestamp >= ? GROUP BY 1;
        """, (start, start))
        buckets = {int(k): (n, b or 0) for k, n, b in c.fetchall()}
        conn.close()
    except Exception as e:
        print(e); return None
    out = []
    for h in range(CANDLES):
        q = [buckets[k] for k in range(h * 4, h * 4 + 4) if k in buckets and buckets[k][0]]
        if not q:
            out.append(None); continue
        p = [b / n * 100 for n, b in q]
        out.append((p[0], max(p), min(p), p[-1], sum(n for n, _ in q)))
    return out

# --- DRAW ---
BG    = (10, 12, 18)
GRID  = (30, 34, 46)
FG    = (230, 232, 236)
DIM   = (120, 128, 142)
UP    = (38, 196, 140)
DOWN  = (239, 83, 80)
VOL   = (52, 60, 80)
TAPE  = (20, 24, 34)
M = 10
CH_X0, CH_X1, CH_Y0, CH_Y1 = M, WIDTH - 44, 64, 222
VOL_Y0, VOL_Y1 = 230, 270

def arrow(draw, x, y, up, color, s=6):
    """Small filled triangle (fonts may lack ▲▼)."""
    if up: draw.polygon([(x, y + s), (x + s, y + s), (x + s / 2, y)], fill=color)
    else:  draw.polygon([(x, y), (x + s, y), (x + s / 2, y + s)], fill=color)

def nice_range(lo, hi):
    lo, hi = max(0, lo - 1), min(100, hi + 1)
    step = next(s for s in (0.5, 1, 2, 5, 10, 20, 25) if (hi - lo) / s <= 5)
    return (lo // step) * step, -(-hi // step) * step, step

def draw_screen(stats):
    cpu, temp, mem = get_cpu(), get_temp(), get_mem()
    ALERT = cpu > 80 or temp > 70
    candles = get_market() or []
    live = [c for c in candles if c]

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # quote header
    price = stats["blocked_pct"] if stats else (live[-1][3] if live else 0)
    first = live[0][0] if live else price
    change = price - first
    color = UP if change >= 0 else DOWN
    text(draw, M, 6, "$PIHL", FG, font_title)
    text(draw, M, 32, "PI-HOLE BLOCK INDEX · %", DIM, font_label)
    pw = text_w(draw, f"{price:.2f}", font_price)
    text(draw, 150, 8, f"{price:.2f}", FG, font_price)
    arrow(draw, 158 + pw, 20, change >= 0, color, 10)
    rel = (change / first * 100) if first else 0
    text(draw, 174 + pw, 14, f"{change:+.2f}  ({rel:+.1f}%)", color, font)
    text(draw, 174 + pw, 32, "24H", DIM, font_label)
    is_open = stats and stats["last"] != "N/A"
    text_right(draw, WIDTH - M, 10, datetime.now().strftime("%H:%M"), DIM, font)
    text_right(draw, WIDTH - M, 30, "MARKET OPEN" if is_open else "MARKET CLOSED",
               UP if is_open else DOWN, font_label)

    # candles
    if live:
        lo, hi, step = nice_range(min(c[2] for c in live), max(c[1] for c in live))
        ys = lambda v: CH_Y1 - (v - lo) / (hi - lo) * (CH_Y1 - CH_Y0)
        v = lo
        while v <= hi + 1e-9:
            y = ys(v)
            draw.line((CH_X0, y, CH_X1, y), fill=GRID)
            text(draw, CH_X1 + 6, y - 6, f"{v:g}%", DIM, font_label)
            v += step
        slot = (CH_X1 - CH_X0) / CANDLES
        bw = max(3, int(slot * 0.62))
        vmax = max(c[4] for c in live)
        for i, c in enumerate(candles):
            if not c: continue
            o, h, l, cl, vol = c
            col = UP if cl >= o else DOWN
            xm = CH_X0 + slot * i + slot / 2
            draw.line((xm, ys(h), xm, ys(l)), fill=col)
            y0, y1 = sorted((ys(o), ys(cl)))
            draw.rectangle((xm - bw / 2, y0, xm + bw / 2, max(y1, y0 + 1)), fill=col)
            vh = (VOL_Y1 - VOL_Y0) * vol / vmax
            draw.rectangle((xm - bw / 2, VOL_Y1 - vh, xm + bw / 2, VOL_Y1), fill=VOL)
        for i in range(0, CANDLES, 6):                         # hour labels
            hr = (datetime.now().hour - (CANDLES - 1 - i)) % 24
            text(draw, CH_X0 + slot * i + 1, VOL_Y1 + 2, f"{hr:02d}h", DIM, font_label)
        text(draw, CH_X1 + 6, VOL_Y0, "VOL", DIM, font_label)
    else:
        text_center(draw, (CH_X0 + CH_X1) // 2, 130, "no trading data", DIM, font)

    # ticker tape
    draw.rectangle((0, 288, WIDTH, HEIGHT), fill=TAPE)
    items = []
    if stats:
        items += [("QRY", f"{stats['total']:,}", True), ("BLK", f"{stats['blocked']:,}", True),
                  ("CLNT", f"{stats['clients']}", True)]
    items += [("CPU", f"{cpu:.0f}%", cpu < 50), ("TEMP", f"{temp:.0f}°C", temp < 60),
              ("MEM", f"{mem:.0f}%", mem < 70)]
    x = M
    for sym, val, good in items:
        if x + text_w(draw, f"{sym} {val}", font_small) + 20 > WIDTH - (60 if ALERT else M):
            break                                          # no room for this symbol
        text(draw, x, 296, sym, DIM, font_small); x += text_w(draw, sym, font_small) + 4
        text(draw, x, 296, val, FG, font_small); x += text_w(draw, val, font_small) + 4
        arrow(draw, x, 300, good, UP if good else DOWN); x += 16
    if ALERT:
        text_right(draw, WIDTH - M, 296, "HALTED", DOWN, font_small)
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
get_market = _every(MARKET_REFRESH, get_market)

# --- LOOP ---
if __name__ == "__main__":
    while True:
        write_fb(draw_screen(get_stats()))
        time.sleep(UPDATE_INTERVAL)
