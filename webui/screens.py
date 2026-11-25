"""User-made screens: the layout format, shared by the web UI (editor, saving) and
lcd_screen.py (drawing on the LCD).

A screen is JSON, never code:
  {"name": "My screen", "bg": "#0b0d12", "widgets": [{"type": "text", "x": 10, ...}, ...]}
Widgets are drawn in list order (later ones on top). sanitize() is the only way a
layout gets saved or drawn: unknown keys are dropped, values are clamped to range.
"""
import os
import re

WIDTH, HEIGHT = 480, 320
MAX_WIDGETS = 80
MAX_SCREENS = 100
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
COLOR_RE = re.compile(r"^#[0-9a-f]{6}$")

# Font faces offered in the editor: first file that exists wins. The web UI serves
# the same file to the browser, so the editor measures text exactly like the LCD.
FONTS = {
    "sans": ["/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
    "mono": ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"],
}


def font_file(face):
    for path in FONTS.get(face, FONTS["sans"]):
        if os.path.isfile(path):
            return path
    return None


# Values a text widget can show as {key}: key -> (label in the editor, sample text)
METRICS = {
    "time":         ("Time", "19:28"),
    "date":         ("Date", "Tue 24 Nov"),
    "host":         ("Hostname", "pihole"),
    "ip":           ("IP address", "192.168.1.53"),
    "queries":      ("Queries today", "29,681"),
    "blocked":      ("Blocked today", "7,196"),
    "allowed":      ("Allowed today", "22,485"),
    "blocked_pct":  ("Blocked %", "24.2%"),
    "clients":      ("Clients today", "14"),
    "last_blocked": ("Last blocked domain", "ads.doubleclick.net"),
    "cpu":          ("CPU", "23%"),
    "mem":          ("Memory", "38%"),
    "disk":         ("Disk", "41%"),
    "temp":         ("Temperature", "52°C"),
    "uptime":       ("Uptime", "12d 7h"),
}

# Values a bar or ring can show: key -> (label, sample 0..100). Temperature is
# shown on a 0-85 °C scale.
PERCENTS = {
    "blocked_pct": ("Blocked %", 24.2),
    "cpu":         ("CPU", 23.0),
    "mem":         ("Memory", 38.0),
    "disk":        ("Disk", 41.0),
    "temp":        ("Temperature (0–85 °C)", 61.6),
}

# What a list widget can show: key -> label
SOURCES = {
    "last_blocked": "Latest blocked domains",
    "top_blocked":  "Top blocked domains today",
    "top_domains":  "Top domains today",
    "top_clients":  "Top clients today",
}

# Sample data so the editor shows something realistic
SAMPLE_HOURLY = [(310, 60), (200, 41), (160, 35), (150, 30), (170, 39), (380, 90), (820, 190),
                 (1300, 320), (1500, 360), (1400, 330), (1300, 310), (1400, 345), (1500, 370),
                 (1380, 330), (1300, 300), (1400, 350), (1600, 390), (1800, 440), (1900, 470),
                 (1880, 455), (1760, 430), (1500, 360), (1000, 240), (560, 130)]
SAMPLE_LISTS = {
    "last_blocked": [("ads.doubleclick.net", None), ("telemetry.microsoft.com", None),
                     ("graph.facebook.com", None), ("app-measurement.com", None),
                     ("analytics.tiktok.com", None), ("pixel.adsafeprotected.com", None),
                     ("ads.yahoo.com", None), ("securepubads.g.doubleclick.net", None)],
    "top_blocked":  [("ads.doubleclick.net", 1032), ("telemetry.microsoft.com", 977),
                     ("graph.facebook.com", 905), ("app-measurement.com", 880),
                     ("securepubads.g.doubleclick.net", 860), ("analytics.tiktok.com", 812),
                     ("pixel.adsafeprotected.com", 790), ("ads.yahoo.com", 744)],
    "top_domains":  [("www.google.com", 3902), ("api.github.com", 3511), ("i.ytimg.com", 3120),
                     ("www.youtube.com", 2804), ("connectivity-check.ubuntu.com", 2410),
                     ("time.apple.com", 2066), ("netflix.com", 1750), ("spotify.com", 1422)],
    "top_clients":  [("living-room-tv", 8810), ("macbook-pro", 6420), ("iphone", 4770),
                     ("nas", 3520), ("work-laptop", 2660), ("ipad", 2030),
                     ("echo-kitchen", 1460), ("printer", 1170)],
}

# Widget types: property -> spec. Specs:
#   ("int", default, min, max)  ("color", default)  ("str", default, maxlen)
#   ("enum", default, choices)  ("bool", default)
_FACES = tuple(FONTS)
TYPES = {
    "rect": {
        "color": ("color", "#1f2430"), "radius": ("int", 8, 0, 160),
        "outline": ("color", "#2a3040"), "outline_w": ("int", 0, 0, 20),
    },
    "text": {
        "text": ("str", "Text", 200), "font": ("enum", "sans", _FACES),
        "size": ("int", 20, 6, 160), "color": ("color", "#e6e8ec"),
        "align": ("enum", "left", ("left", "center", "right")),
    },
    "bar": {
        "metric": ("enum", "blocked_pct", tuple(PERCENTS)), "color": ("color", "#ef5350"),
        "track": ("color", "#2a3040"), "radius": ("int", 4, 0, 80),
    },
    "ring": {
        "metric": ("enum", "blocked_pct", tuple(PERCENTS)), "color": ("color", "#ef5350"),
        "track": ("color", "#2a3040"), "thickness": ("int", 12, 1, 80),
        "show_value": ("bool", True), "text_color": ("color", "#e6e8ec"),
    },
    "chart": {
        "color": ("color", "#42a5f5"), "color2": ("color", "#ef5350"),
    },
    "list": {
        "source": ("enum", "last_blocked", tuple(SOURCES)), "font": ("enum", "sans", _FACES),
        "size": ("int", 14, 6, 60), "color": ("color", "#e6e8ec"), "color2": ("color", "#8a93a3"),
    },
}
GEOMETRY = {"x": ("int", 0, -WIDTH, 2 * WIDTH), "y": ("int", 0, -HEIGHT, 2 * HEIGHT),
            "w": ("int", 100, 1, 2 * WIDTH), "h": ("int", 40, 1, 2 * HEIGHT)}
DEFAULT_SIZE = {"rect": (160, 90), "text": (200, 30), "bar": (200, 14), "ring": (100, 100),
                "chart": (300, 90), "list": (240, 120)}

STARTER = {
    "name": "My screen", "bg": "#0b0d12",
    "widgets": [
        {"type": "text", "x": 14, "y": 10, "w": 250, "h": 26, "text": "{host}", "size": 22},
        {"type": "text", "x": 266, "y": 14, "w": 200, "h": 20, "text": "{date}  {time}",
         "size": 15, "color": "#8a93a3", "align": "right"},
        {"type": "rect", "x": 10, "y": 46, "w": 225, "h": 110, "color": "#171b23"},
        {"type": "text", "x": 24, "y": 56, "w": 200, "h": 14, "text": "BLOCKED TODAY",
         "size": 11, "color": "#8a93a3"},
        {"type": "text", "x": 24, "y": 74, "w": 200, "h": 50, "text": "{blocked_pct}",
         "size": 44, "color": "#ef5350"},
        {"type": "bar", "x": 24, "y": 132, "w": 197, "h": 10},
        {"type": "rect", "x": 245, "y": 46, "w": 225, "h": 110, "color": "#171b23"},
        {"type": "ring", "x": 255, "y": 56, "w": 90, "h": 90, "metric": "cpu",
         "color": "#42a5f5", "thickness": 10},
        {"type": "text", "x": 355, "y": 66, "w": 110, "h": 16, "text": "{queries} queries",
         "size": 13},
        {"type": "text", "x": 355, "y": 88, "w": 110, "h": 16, "text": "{clients} clients",
         "size": 13},
        {"type": "text", "x": 355, "y": 110, "w": 110, "h": 16, "text": "{temp}", "size": 13,
         "color": "#66bb6a"},
        {"type": "rect", "x": 10, "y": 166, "w": 460, "h": 144, "color": "#171b23"},
        {"type": "text", "x": 24, "y": 176, "w": 300, "h": 14, "text": "QUERIES · LAST 24 HOURS",
         "size": 11, "color": "#8a93a3"},
        {"type": "chart", "x": 24, "y": 198, "w": 432, "h": 98},
    ],
}


def _clean(spec, value):
    kind, default = spec[0], spec[1]
    if kind == "int":
        try:
            return max(spec[2], min(spec[3], int(round(float(value)))))
        except (TypeError, ValueError, OverflowError):
            return default
    if kind == "color":
        return value.lower() if isinstance(value, str) and COLOR_RE.match(value.lower()) else default
    if kind == "str":
        return value.replace("\n", " ")[:spec[2]] if isinstance(value, str) else default
    if kind == "enum":
        return value if value in spec[2] else default
    if kind == "bool":
        return value if isinstance(value, bool) else default
    return default


def new_widget(kind):
    w, h = DEFAULT_SIZE[kind]
    out = {"type": kind, "x": (WIDTH - w) // 2, "y": (HEIGHT - h) // 2, "w": w, "h": h}
    out.update({k: spec[1] for k, spec in TYPES[kind].items()})
    return out


def sanitize(layout):
    """A clean copy of layout, or ValueError if it isn't a screen at all."""
    if not isinstance(layout, dict) or not isinstance(layout.get("widgets"), list):
        raise ValueError("not a screen layout")
    if len(layout["widgets"]) > MAX_WIDGETS:
        raise ValueError(f"too many widgets (at most {MAX_WIDGETS})")
    name = _clean(("str", "", 40), layout.get("name")).strip() or "My screen"
    out = {"name": name, "bg": _clean(("color", "#000000"), layout.get("bg")), "widgets": []}
    for w in layout["widgets"]:
        if not isinstance(w, dict) or w.get("type") not in TYPES:
            continue
        clean = {"type": w["type"]}
        for k, spec in {**GEOMETRY, **TYPES[w["type"]]}.items():
            clean[k] = _clean(spec, w.get(k, spec[1]))
        out["widgets"].append(clean)
    return out


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32].strip("-")
    return s or "screen"


def meta():
    """Everything the editor needs to know, as JSON-friendly data."""
    return {
        "width": WIDTH, "height": HEIGHT, "max_widgets": MAX_WIDGETS,
        "metrics": {k: list(v) for k, v in METRICS.items()},
        "percents": {k: list(v) for k, v in PERCENTS.items()},
        "sources": SOURCES, "fonts": list(FONTS),
        "types": {t: {k: list(spec) for k, spec in props.items()} for t, props in TYPES.items()},
        "new": {t: new_widget(t) for t in TYPES},
        "sample": {"hourly": SAMPLE_HOURLY, "lists": SAMPLE_LISTS},
        "starter": sanitize(STARTER),
    }
