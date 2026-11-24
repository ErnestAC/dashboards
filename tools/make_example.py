#!/usr/bin/env python3
"""Make the example PNG for one or more LCD dashboards, using sample data.

usage:
  tools/make_example.py lcd_180_neon.py [more.py ...]   # writes lcd_180_neon.png next to it
  tools/make_example.py --all                          # every dashboard in dashboards/
  options:
    --out-dir DIR     write the PNGs to DIR instead of next to each script
    --font FILE       use FILE for any font the script asks for that isn't installed
                      (e.g. Roboto on a machine that doesn't have it)

The web UI shows these images as previews; nothing is rendered on the server.
Run this on any machine: it never touches a screen or a real Pi-hole.

Sample data (the same every run, apart from the clock):
  - a temporary Pi-hole database with 7 days of queries (~24% blocked,
    quiet nights, busy evenings), 14 clients with hostnames, lifetime counters
  - hostname "pihole", IP 192.168.1.53, CPU 23%, memory 38%, disk 41%, 52.4 °C,
    uptime 12d 7h 43m
"""
import argparse
import builtins
import glob
import importlib.util
import io
import os
import random
import socket
import sqlite3
import sys
import tempfile
import time
import traceback
from datetime import datetime as _datetime

sys.dont_write_bytecode = True          # leave no __pycache__ in the project

DASH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIHOLE_DB = "/etc/pihole/pihole-FTL.db"

HOSTNAME, IP = "pihole", "192.168.1.53"
CPU, MEM, DISK = 23.0, 38.0, 41.0
FAKE_FILES = {
    "/proc/uptime": "1064580.12 4000000.00\n",                 # 12d 7h 43m
    "/sys/class/thermal/thermal_zone0/temp": "52400\n",         # 52.4 °C
}


# ------------------------------------------------------------ sample data
def build_db(path, seed=42):
    """Pi-hole-like database: queries, client_by_id, counters."""
    rnd = random.Random(seed)
    now = int(time.time())

    allowed = ["www.google.com", "api.github.com", "i.ytimg.com", "www.youtube.com",
               "connectivity-check.ubuntu.com", "time.apple.com", "netflix.com",
               "spotify.com", "cdn.discordapp.com", "en.wikipedia.org"]
    blocked = ["ads.doubleclick.net", "telemetry.microsoft.com", "graph.facebook.com",
               "app-measurement.com", "securepubads.g.doubleclick.net",
               "analytics.tiktok.com", "pixel.adsafeprotected.com", "ads.yahoo.com"]
    clients = [f"192.168.1.{i}" for i in (10, 11, 12, 20, 21, 22, 30, 31, 40, 41, 50, 51, 60, 61)]
    names = ["living-room-tv", "macbook-pro", "iphone", "nas", "work-laptop", "ipad",
             "echo-kitchen", "printer", "thermostat", "doorbell", "xbox", "pixel", "", ""]
    client_w = [30, 22, 16, 12, 9, 7, 5, 4, 3, 3, 2, 2, 1, 1]
    hour_w = [.15, .1, .08, .08, .1, .2, .45, .7, .8, .75, .7, .75,
              .8, .75, .7, .75, .85, .95, 1, 1, .95, .8, .55, .3]

    def sample_ts(lo, hi):
        while True:
            ts = rnd.randint(lo, hi)
            if rnd.random() < hour_w[time.localtime(ts).tm_hour]:
                return ts

    rows = []
    def add(ts):
        if rnd.random() < 0.245:
            status, domain = 1, rnd.choice(blocked)
        else:
            status, domain = rnd.choice((2, 3)), rnd.choices(allowed, weights=range(10, 0, -1))[0]
        rows.append((ts, 1 if rnd.random() < 0.61 else 28, status, domain,
                     rnd.choices(clients, weights=client_w)[0], None, 4, rnd.uniform(0.002, 0.06)))

    # one continuous week at ~30k queries/day (weekends busier), so charts look
    # natural whatever the time of day; "today" is whatever falls after midnight UTC
    for day in range(7):
        hi = now - 120 - day * 86400
        lo = hi - 86400 + 1
        vol = rnd.uniform(0.85, 1.15) * (1.2 if time.localtime(lo).tm_wday >= 5 else 1)
        for _ in range(int(30000 * vol)):
            add(sample_ts(lo, hi))
    for i, d in enumerate(reversed(blocked[:7])): # a known, varied "latest blocked" list
        rows.append((now - 60 + i * 5, 1, 1, d, clients[0], None, 4, 0.004))

    con = sqlite3.connect(path)
    con.execute("CREATE TABLE queries (id INTEGER PRIMARY KEY, timestamp INTEGER, type INTEGER,"
                " status INTEGER, domain TEXT, client TEXT, forward TEXT, reply_type INTEGER,"
                " reply_time REAL)")
    con.execute("CREATE INDEX idx_queries_timestamp ON queries (timestamp)")
    con.executemany("INSERT INTO queries (timestamp, type, status, domain, client, forward,"
                    " reply_type, reply_time) VALUES (?,?,?,?,?,?,?,?)", rows)
    con.execute("CREATE TABLE client_by_id (id INTEGER PRIMARY KEY, ip TEXT, name TEXT)")
    con.executemany("INSERT INTO client_by_id (ip, name) VALUES (?,?)", zip(clients, names))
    con.execute("CREATE TABLE counters (id INTEGER PRIMARY KEY, value INTEGER)")
    con.executemany("INSERT INTO counters VALUES (?,?)", [(0, 1284512), (1, 301220)])
    con.commit()
    con.close()


# ------------------------------------------------------- sandbox patches
class _StopRender(Exception):
    pass


class _FramebufferSink(io.BytesIO):
    """Stands in for /dev/fb*: stops scripts whose display loop starts on import."""
    def close(self):
        if not getattr(self, "_stopped", False):
            self._stopped = True
            raise _StopRender

    def __exit__(self, *exc):
        self.close()


def _fake_open(path, *args, **kwargs):
    if path in FAKE_FILES:
        return io.StringIO(FAKE_FILES[path])
    if isinstance(path, str) and path.startswith("/dev/fb"):
        return _FramebufferSink()
    return builtins.open(path, *args, **kwargs)


def install_patches(db_path, font_fallback, now):
    import psutil
    from PIL import ImageFont

    class _VM:
        percent = MEM
    psutil.cpu_percent = lambda *a, **k: CPU
    psutil.virtual_memory = lambda: _VM()

    class _DU:
        percent = DISK
    psutil.disk_usage = lambda *a, **k: _DU()
    socket.gethostname = lambda: HOSTNAME
    time.time = lambda: now.timestamp()

    real_connect = sqlite3.connect
    def connect(path, *a, **k):
        return real_connect(db_path if path == PIHOLE_DB else path, *a, **k)
    sqlite3.connect = connect

    real_truetype = ImageFont.truetype
    missing = set()
    def truetype(font=None, size=10, *a, **k):
        if isinstance(font, str) and not os.path.exists(font):
            if not font_fallback:
                missing.add(font)
                raise OSError(f"font not installed: {font} (use --font FILE)")
            font = font_fallback
        return real_truetype(font, size, *a, **k)
    ImageFont.truetype = truetype


def frozen_datetime(now):
    class FixedDatetime(_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(now.year, now.month, now.day, now.hour, now.minute)
    return FixedDatetime


def render(script, now):
    spec = importlib.util.spec_from_file_location("dashboard_" + os.path.basename(script)[:-3], script)
    module = importlib.util.module_from_spec(spec)
    module.open = _fake_open                      # the script's own open()
    try:
        spec.loader.exec_module(module)
    except _StopRender:
        pass                                      # module-level loop reached write_fb
    if hasattr(module, "datetime"):
        module.datetime = frozen_datetime(now)
    if hasattr(module, "get_ip"):
        module.get_ip = lambda: IP
    return module.draw_screen(module.get_stats())


# ------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scripts", nargs="*", help="dashboard scripts (names or paths)")
    ap.add_argument("--all", action="store_true", help="every dashboard in dashboards/")
    ap.add_argument("--out-dir", help="write PNGs here instead of next to each script")
    ap.add_argument("--font", help="font file to use for fonts that aren't installed")
    args = ap.parse_args()

    if args.all:
        scripts = sorted(glob.glob(os.path.join(DASH_DIR, "*.py")))
    else:
        scripts = [s if os.path.exists(s) else os.path.join(DASH_DIR, s) for s in args.scripts]
    if not scripts:
        ap.error("name at least one dashboard, or use --all")
    if args.font and not os.path.isfile(args.font):
        ap.error(f"--font: no such file {args.font}")

    now = _datetime.now().replace(second=0, microsecond=0)
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "pihole-FTL.db")
        build_db(db)
        install_patches(db, args.font, now)
        for script in scripts:
            name = os.path.basename(script)
            if not os.path.isfile(script):
                print(f"FAIL {name}: no such file"); failed += 1; continue
            out = os.path.join(args.out_dir, name[:-3] + ".png") if args.out_dir \
                else script[:-3] + ".png"
            try:
                img = render(script, now)
                if args.out_dir:
                    os.makedirs(args.out_dir, exist_ok=True)
                img.save(out)
                print(f"ok   {name:34s} -> {os.path.relpath(out)}  {img.size[0]}x{img.size[1]}")
            except Exception as e:
                failed += 1
                print(f"FAIL {name}: {e}")
                if not isinstance(e, OSError):
                    traceback.print_exc()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
