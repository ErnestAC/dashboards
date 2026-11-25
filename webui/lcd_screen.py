#!/usr/bin/env python3
"""Draw a user-made screen (a layout saved by the web UI's editor) on the LCD.

usage: lcd_screen.py <layout.json>

lcd_runner.py starts this for dashboards named "custom:<id>". The layout is data,
checked by screens.sanitize(); it is reloaded when the file changes, so saving in
the editor updates the screen within a couple of seconds. The drawing rules here
match the editor's (webui/editor.html), so the LCD shows what the editor showed.
"""
import math
import os
import re
import socket
import sqlite3
import sys
import time
from datetime import datetime

from PIL import Image, ImageChops, ImageDraw, ImageFont

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import screens  # noqa: E402

try:
    import psutil
except ImportError:
    psutil = None

FB_DEV = os.environ.get("LCD_FB_DEV", "/dev/fb1")
DB_PATH = "/etc/pihole/pihole-FTL.db"
UPDATE_INTERVAL = 2
STATS_TTL = 30
W, H = screens.WIDTH, screens.HEIGHT

# Every Pi-hole status that means "blocked" (gravity, regex, denylist, their CNAME
# variants, upstream-blocked, database busy, special domains)
BLOCKED = "status IN (1,4,5,6,7,8,9,10,11,15,16)"
TODAY = "strftime('%s','now','localtime','start of day','utc')"


# ------------------------------------------------------------------ data
def _every(seconds, fn):
    cache = {}
    def wrapper(*args):
        hit = cache.get(args)
        if hit is None or time.time() - hit[0] >= seconds:
            hit = cache[args] = (time.time(), fn(*args))
        return hit[1]
    return wrapper


def _query(sql, args=()):
    conn = sqlite3.connect(DB_PATH, timeout=5)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def get_stats():
    try:
        (total, blocked, clients, last), = _query(
            f"SELECT COUNT(*), SUM(CASE WHEN {BLOCKED} THEN 1 ELSE 0 END), COUNT(DISTINCT client),"
            f" (SELECT domain FROM queries WHERE {BLOCKED} ORDER BY timestamp DESC LIMIT 1)"
            f" FROM queries WHERE timestamp >= {TODAY}")
        return {"total": total or 0, "blocked": blocked or 0, "clients": clients or 0,
                "last_blocked": last or "—"}
    except Exception as e:
        print("stats:", e)
        return None


def get_hourly():
    """Last 24 hours, oldest first: [(total, blocked)]."""
    hours = [(0, 0)] * 24
    try:
        now = int(time.time())
        start = now - now % 3600 - 23 * 3600
        for h, n, b in _query(f"SELECT (timestamp - ?) / 3600, COUNT(*),"
                              f" SUM(CASE WHEN {BLOCKED} THEN 1 ELSE 0 END)"
                              f" FROM queries WHERE timestamp >= ? GROUP BY 1", (start, start)):
            if 0 <= h < 24:
                hours[h] = (n, b or 0)
    except Exception as e:
        print("hourly:", e)
    return hours


def get_list(source):
    """[(label, count or None)] for a list widget."""
    try:
        if source == "last_blocked":
            return [(d, None) for d, in _query(
                f"SELECT domain FROM queries WHERE {BLOCKED} AND timestamp >= strftime('%s','now','-1 day')"
                " GROUP BY domain ORDER BY MAX(timestamp) DESC LIMIT 12")]
        if source == "top_blocked":
            return _query(f"SELECT domain, COUNT(*) FROM queries WHERE {BLOCKED} AND timestamp >= {TODAY}"
                          " GROUP BY domain ORDER BY 2 DESC LIMIT 12")
        if source == "top_domains":
            return _query(f"SELECT domain, COUNT(*) FROM queries WHERE timestamp >= {TODAY}"
                          " GROUP BY domain ORDER BY 2 DESC LIMIT 12")
        if source == "top_clients":
            rows = _query(f"SELECT client, COUNT(*) FROM queries WHERE timestamp >= {TODAY}"
                          " GROUP BY client ORDER BY 2 DESC LIMIT 12")
            try:
                names = dict(_query("SELECT ip, name FROM client_by_id WHERE name IS NOT NULL AND name != ''"))
            except sqlite3.Error:
                names = {}
            return [(names.get(ip, ip), n) for ip, n in rows]
    except Exception as e:
        print("list:", e)
    return []


def get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]; s.close(); return ip
    except OSError:
        return "N/A"


def get_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read()) / 1000
    except (OSError, ValueError):
        return 0.0


def get_uptime():
    try:
        with open("/proc/uptime") as f:
            t = int(float(f.read().split()[0]))
        d, h, m = t // 86400, (t // 3600) % 24, (t // 60) % 60
        return f"{d}d {h}h" if d else f"{h}h {m}m"
    except (OSError, ValueError):
        return "N/A"


# database answers change slowly: reuse them for STATS_TTL seconds
get_stats = _every(STATS_TTL, get_stats)
get_hourly = _every(STATS_TTL, get_hourly)
get_list = _every(STATS_TTL, get_list)
get_ip = _every(300, get_ip)


def values():
    """(text values for {placeholders}, 0..100 values for bars and rings)."""
    now = datetime.now()
    st = get_stats()
    cpu = psutil.cpu_percent() if psutil else 0.0
    mem = psutil.virtual_memory().percent if psutil else 0.0
    disk = psutil.disk_usage("/").percent if psutil else 0.0
    temp = get_temp()
    pct = st["blocked"] / st["total"] * 100 if st and st["total"] else 0.0
    text = {
        "time": now.strftime("%H:%M"), "date": now.strftime("%a %d %b"),
        "host": socket.gethostname(), "ip": get_ip(),
        "queries": f"{st['total']:,}" if st else "—",
        "blocked": f"{st['blocked']:,}" if st else "—",
        "allowed": f"{st['total'] - st['blocked']:,}" if st else "—",
        "blocked_pct": f"{pct:.1f}%" if st else "—",
        "clients": f"{st['clients']}" if st else "—",
        "last_blocked": st["last_blocked"] if st else "—",
        "cpu": f"{cpu:.0f}%", "mem": f"{mem:.0f}%", "disk": f"{disk:.0f}%",
        "temp": f"{temp:.0f}°C", "uptime": get_uptime(),
    }
    percent = {"blocked_pct": pct, "cpu": cpu, "mem": mem, "disk": disk,
               "temp": max(0.0, min(100.0, temp / 85 * 100))}
    return text, percent


# ---------------------------------------------------------------- drawing
# Keep these in step with the drawing functions in editor.html.
_fonts = {}


def font(face, size):
    key = (face, size)
    if key not in _fonts:
        path = screens.font_file(face)
        _fonts[key] = ImageFont.truetype(path, size) if path else ImageFont.load_default()
    return _fonts[key]


def rnd(v):
    return int(math.floor(v + 0.5))


def rgb(c):
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def fit(s, f, maxw):
    if f.getlength(s) <= maxw:
        return s
    while s and f.getlength(s + "…") > maxw:
        s = s[:-1]
    return s + "…" if s else ""


def fill_text(template, vals):
    return re.sub(r"\{(\w+)\}", lambda m: vals.get(m[1], m[0]), template)


def rounded(draw, x, y, w, h, r, fill=None, outline=None, width=0):
    if w <= 0 or h <= 0:
        return
    r = max(0, min(r, w // 2, h // 2))
    box = (x, y, x + w - 1, y + h - 1)
    if hasattr(draw, "rounded_rectangle"):
        draw.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)
    else:
        draw.rectangle(box, fill=fill, outline=outline, width=width)


def draw_widget(draw, w, text_vals, pct_vals):
    x, y, ww, hh, t = w["x"], w["y"], w["w"], w["h"], w["type"]
    if t == "rect":
        ow = w["outline_w"]
        rounded(draw, x, y, ww, hh, w["radius"], fill=rgb(w["color"]),
                outline=rgb(w["outline"]) if ow else None, width=ow)
    elif t == "text":
        f = font(w["font"], w["size"])
        s = fit(fill_text(w["text"], text_vals), f, ww)
        ax, anchor = {"left": (x, "la"), "center": (x + ww / 2, "ma"), "right": (x + ww, "ra")}[w["align"]]
        draw.text((ax, y), s, fill=rgb(w["color"]), font=f, anchor=anchor)
    elif t == "bar":
        r = min(w["radius"], hh // 2)
        rounded(draw, x, y, ww, hh, r, fill=rgb(w["track"]))
        fw = rnd(ww * max(0.0, min(100.0, pct_vals[w["metric"]])) / 100)
        if fw > 0:
            rounded(draw, x, y, fw, hh, min(r, fw // 2), fill=rgb(w["color"]))
    elif t == "ring":
        side = min(ww, hh)
        bx, by = x + (ww - side) // 2, y + (hh - side) // 2
        th = max(1, min(w["thickness"], side // 2))
        box = (bx, by, bx + side - 1, by + side - 1)
        draw.ellipse(box, outline=rgb(w["track"]), width=th)
        sweep = 360 * max(0.0, min(100.0, pct_vals[w["metric"]])) / 100
        if sweep > 0:
            draw.arc(box, -90, -90 + sweep, fill=rgb(w["color"]), width=th)
        if w["show_value"]:
            f = font("sans", max(6, rnd(side * 0.22)))
            draw.text((bx + side / 2, by + side / 2), text_vals.get(w["metric"], ""),
                      fill=rgb(w["text_color"]), font=f, anchor="mm")
    elif t == "chart":
        hours = get_hourly()
        gap = 2 if ww >= 120 else 1
        bw = (ww - 23 * gap) / 24
        top = max((n for n, _ in hours), default=0) or 1
        for i, (n, b) in enumerate(hours):
            hn, hb = rnd(n / top * hh), rnd(b / top * hh)
            x0 = rnd(x + i * (bw + gap)); x1 = rnd(x + i * (bw + gap) + bw) - 1
            if hn > 0 and x1 >= x0:
                draw.rectangle((x0, y + hh - hn, x1, y + hh - 1), fill=rgb(w["color"]))
            if hb > 0 and x1 >= x0:
                draw.rectangle((x0, y + hh - hn, x1, y + hh - hn + hb - 1), fill=rgb(w["color2"]))
    elif t == "list":
        f = font(w["font"], w["size"])
        lh = rnd(w["size"] * 1.4)
        rows = max(1, hh // lh)
        for i, (label, count) in enumerate(get_list(w["source"])[:rows]):
            ly = y + i * lh
            room = ww
            if count is not None:
                c = f"{count:,}"
                draw.text((x + ww, ly), c, fill=rgb(w["color2"]), font=f, anchor="ra")
                room = ww - f.getlength(c) - 8
            draw.text((x, ly), fit(str(label), f, room), fill=rgb(w["color"]), font=f, anchor="la")


def render(layout):
    img = Image.new("RGB", (W, H), rgb(layout["bg"]))
    draw = ImageDraw.Draw(img)
    text_vals, pct_vals = values()
    for w in layout["widgets"]:
        try:
            draw_widget(draw, w, text_vals, pct_vals)
        except Exception as e:          # one bad widget shouldn't blank the screen
            print(f"widget {w.get('type')}: {e}")
    return img


# ---------------------------------------------------------- framebuffer
_R_HI = [v & 0xF8 for v in range(256)]
_G_HI = [v >> 5 for v in range(256)]
_G_LO = [(v & 0x1C) << 3 for v in range(256)]
_B_LO = [v >> 3 for v in range(256)]
FB_REFRESH = 60
_last_frame, _last_write = None, 0.0


def write_fb(img):
    """img to the framebuffer as little-endian RGB565; unchanged frames are skipped."""
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


def load(path):
    import json
    with open(path) as f:
        return screens.sanitize(json.load(f))


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    path = sys.argv[1]
    layout, mtime = load(path), os.path.getmtime(path)
    if psutil:
        psutil.cpu_percent()             # the first reading is always 0
    while True:
        try:
            m = os.path.getmtime(path)
            if m != mtime:
                layout, mtime = load(path), m
                print("layout reloaded")
        except (OSError, ValueError) as e:
            print("keeping the current layout:", e)
        write_fb(render(layout))
        time.sleep(UPDATE_INTERVAL)


if __name__ == "__main__":
    main()
