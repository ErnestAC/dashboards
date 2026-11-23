#!/usr/bin/env python3
"""Pi-hole LCD (480x320): retro terminal. Green phosphor monospace, ASCII bars; turns amber when the system runs hot."""
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
        SELECT DISTINCT domain FROM queries
        WHERE status=1
        ORDER BY timestamp DESC
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
MONO_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
mono = ImageFont.truetype(MONO_PATH, 13)

# --- DRAW ---
# Phosphor palettes: green normally, amber when the system runs hot
GREEN_PH = {"bg": (0, 8, 2),  "hi": (90, 255, 130), "fg": (51, 220, 102), "dim": (24, 120, 56),  "faint": (14, 60, 30)}
AMBER_PH = {"bg": (10, 6, 0), "hi": (255, 210, 90), "fg": (255, 176, 0),  "dim": (150, 100, 0), "faint": (70, 46, 0)}

LH, X0, Y0 = 16, 10, 6   # line height, left margin, top margin

def ascii_bar(pct, width=20):
    n = int(round(max(0, min(100, pct)) / 100 * width))
    return "#" * n, "." * (width - n)

def draw_screen(stats):
    cpu, temp, mem = get_cpu(), get_temp(), get_mem()
    ALERT = cpu > 80 or temp > 70
    P = AMBER_PH if ALERT else GREEN_PH

    img = Image.new("RGB", (WIDTH, HEIGHT), P["bg"])
    draw = ImageDraw.Draw(img)
    cw = text_w(draw, "M", mono)          # monospace cell width

    def put(col, row, s, c):
        draw.text((X0 + col * cw, Y0 + row * LH), s, fill=c, font=mono)

    def bar_row(row, label, pct, value):
        on, off = ascii_bar(pct)
        put(0, row, f"{label:<5}[", P["fg"])
        put(6, row, on, P["hi"])
        put(6 + len(on), row, off, P["faint"])
        put(26, row, "]", P["fg"])
        put(28, row, value, P["hi"])

    # title bar (inverse video)
    draw.rectangle((0, 0, WIDTH, Y0 + LH - 2), fill=P["fg"])
    put(0, 0, f"{get_hostname()}:~", P["bg"])
    clock = datetime.now().strftime("%a %d %b %H:%M")
    draw.text((WIDTH - X0 - text_w(draw, clock, mono), Y0), clock, fill=P["bg"], font=mono)

    put(0, 1.3, "$ sysinfo" + ("   !! SYSTEM HOT" if ALERT else ""), P["dim"])
    bar_row(2.3, "cpu", cpu, f"{cpu:5.1f}%")
    bar_row(3.3, "mem", mem, f"{mem:5.1f}%")
    bar_row(4.3, "temp", temp / 85 * 100, f"{temp:5.1f}C")
    put(0, 5.3, f"up   {get_uptime_short():<14}ip   {get_ip()}", P["fg"])

    put(0, 6.6, "$ pihole -c", P["dim"])
    if stats:
        put(0, 7.6, f"queries {stats['total']:>8,}    clients {stats['clients']:>6}", P["fg"])
        put(0, 8.6, f"blocked {stats['blocked']:>8,}    last    {stats['last']:>6}", P["fg"])
        bar_row(9.6, "rate", stats["blocked_pct"], f"{stats['blocked_pct']:5.1f}%")

        put(0, 10.9, "$ tail -n6 blocked.log", P["dim"])
        maxc = (WIDTH - 2 * X0) // cw - 2
        for i, d in enumerate(stats["last_blocked"][:6]):
            s = d if len(d) <= maxc else d[:maxc - 1] + "~"
            put(0, 11.9 + i, "x", P["dim"])
            put(2, 11.9 + i, s, P["fg"])
        prompt_row = 11.9 + min(6, len(stats["last_blocked"]))
    else:
        put(0, 7.6, "error: cannot open pihole-FTL.db", P["hi"])
        prompt_row = 8.6

    # blinking cursor: on for even seconds
    put(0, prompt_row, "$", P["dim"])
    if int(time.time()) % 2 == 0:
        x = X0 + 2 * cw
        y = int(Y0 + prompt_row * LH)
        draw.rectangle((x, y + 2, x + cw - 1, y + LH - 3), fill=P["fg"])
    return img

# --- FB ---
def write_fb(img):
    img = img.convert("RGB")
    with open(FB_DEV, "wb") as f:
        for y in range(HEIGHT):
            for x in range(WIDTH):
                r, g, b = img.getpixel((x, y))
                rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                f.write(rgb565.to_bytes(2, "little"))

# --- LOOP ---
if __name__ == "__main__":
    while True:
        write_fb(draw_screen(get_stats()))
        time.sleep(UPDATE_INTERVAL)
