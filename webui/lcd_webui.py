#!/usr/bin/env python3
"""Password-protected web UI to preview and switch the LCD dashboard.

usage:
  lcd_webui.py                 run the web server
  lcd_webui.py set-password    set or change the login password

It lists the dashboard scripts with their pre-generated example images
(<script>.png next to each script; nothing is rendered here), shows what is
on the physical screen now (read back from the framebuffer), and switches
the dashboard by writing the script name to $LCD_STATE_DIR/active, which
lcd_runner.py picks up. Only the applied dashboard ever renders, on the device.

Environment:
  LCD_DASH_DIR    folder with the dashboard scripts (default: parent of this file)
  LCD_STATE_DIR   shared state folder (default: /var/lib/lcd-dashboard)
  LCD_WEBUI_BIND  address to listen on (default: 0.0.0.0)
  LCD_WEBUI_PORT  port (default: 8911)
  LCD_FB_DEV      framebuffer for the live view (default: /dev/fb1)
"""
import ast
import getpass
import hashlib
import hmac
import io
import json
import os
import secrets
import socket
import sys
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
DASH_DIR = os.path.abspath(os.environ.get("LCD_DASH_DIR", os.path.join(HERE, "..")))
STATE_DIR = os.environ.get("LCD_STATE_DIR", "/var/lib/lcd-dashboard")
BIND = os.environ.get("LCD_WEBUI_BIND", "0.0.0.0")
PORT = int(os.environ.get("LCD_WEBUI_PORT", "8911"))
FB_DEV = os.environ.get("LCD_FB_DEV", "/dev/fb1")

ACTIVE = os.path.join(STATE_DIR, "active")
STATUS = os.path.join(STATE_DIR, "status.json")
PASSWORD_FILE = os.path.join(STATE_DIR, "webui.passwd")

SESSION_TTL = 7 * 86400
COOKIE = "lcd_session"
PBKDF2_ITERS = 120_000


# --------------------------------------------------------------- password
def hash_password(pw, salt=None, iters=PBKDF2_ITERS):
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, iters)
    return f"pbkdf2_sha256${iters}${salt.hex()}${dk.hex()}"


def check_password(pw):
    try:
        with open(PASSWORD_FILE) as f:
            algo, iters, salt, want = f.read().strip().split("$")
    except (FileNotFoundError, ValueError):
        return False
    if algo != "pbkdf2_sha256":
        return False
    got = hash_password(pw, bytes.fromhex(salt), int(iters)).split("$")[3]
    return hmac.compare_digest(got, want)


def set_password():
    pw = getpass.getpass("New web UI password: ")
    if len(pw) < 8:
        sys.exit("Use at least 8 characters.")
    if getpass.getpass("Repeat: ") != pw:
        sys.exit("Passwords don't match.")
    os.makedirs(STATE_DIR, exist_ok=True)
    fd = os.open(PASSWORD_FILE + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(hash_password(pw) + "\n")
    os.replace(PASSWORD_FILE + ".tmp", PASSWORD_FILE)
    print(f"Password saved to {PASSWORD_FILE}")


# --------------------------------------------------------------- sessions
_sessions = {}              # token -> {"exp": t, "csrf": str}
_sessions_lock = threading.Lock()
_failures = []              # timestamps of failed logins
_failures_lock = threading.Lock()


def new_session():
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    with _sessions_lock:
        now = time.time()
        for t in [t for t, s in _sessions.items() if s["exp"] < now]:
            del _sessions[t]
        _sessions[token] = {"exp": now + SESSION_TTL, "csrf": csrf}
    return token


def get_session(token):
    with _sessions_lock:
        s = _sessions.get(token or "")
        if s and s["exp"] > time.time():
            return s
    return None


def login_locked():
    with _failures_lock:
        now = time.time()
        _failures[:] = [t for t in _failures if now - t < 60]
        return len(_failures) >= 5


def note_failure():
    with _failures_lock:
        _failures.append(time.time())


# ------------------------------------------------------------- dashboards
def valid_name(name):
    return (bool(name) and "/" not in name and not name.startswith(".")
            and name.endswith(".py") and os.path.isfile(os.path.join(DASH_DIR, name)))


_desc_cache = {}


def describe(name):
    """First line of the script's docstring, if it has one (parsed, not run)."""
    path = os.path.join(DASH_DIR, name)
    mtime = os.path.getmtime(path)
    hit = _desc_cache.get(name)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        with open(path) as f:
            doc = ast.get_docstring(ast.parse(f.read())) or ""
    except (SyntaxError, ValueError, OSError):
        doc = ""
    desc = doc.strip().splitlines()[0] if doc.strip() else ""
    _desc_cache[name] = (mtime, desc)
    return desc


def list_dashboards():
    names = sorted(n for n in os.listdir(DASH_DIR) if valid_name(n))
    out = []
    for n in names:
        p = preview_path(n)
        out.append({"name": n, "desc": describe(n),
                    "preview": int(os.path.getmtime(p)) if os.path.isfile(p) else None})
    return out


def preview_path(name):
    """The pre-generated example image that sits next to the script."""
    return os.path.join(DASH_DIR, name[:-3] + ".png")


def read_active():
    try:
        with open(ACTIVE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def read_status():
    try:
        with open(STATUS) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {"state": "unknown"}


def set_active(name):
    tmp = f"{ACTIVE}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        f.write(name + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, ACTIVE)


# ------------------------------------------------------------ live screen
def screen_png():
    """What is on the physical screen now, read back from the framebuffer (RGB565)."""
    from PIL import Image
    fb = os.path.basename(FB_DEV)
    sysfs = f"/sys/class/graphics/{fb}"
    try:
        with open(f"{sysfs}/virtual_size") as f:
            w, h = (int(v) for v in f.read().strip().split(","))
        with open(f"{sysfs}/bits_per_pixel") as f:
            bpp = int(f.read())
        try:
            with open(f"{sysfs}/stride") as f:
                stride = int(f.read())
        except OSError:
            stride = w * 2
        if bpp != 16:
            return None
        with open(FB_DEV, "rb") as f:
            data = f.read(stride * h)
    except (OSError, ValueError):
        return None
    if len(data) < stride * h:
        return None
    img = Image.frombytes("RGB", (w, h), data, "raw", "BGR;16", stride)
    buf = io.BytesIO(); img.save(buf, "PNG")
    return buf.getvalue()


# ------------------------------------------------------------------- HTTP
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; "
                               "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "lcd-webui"
    sys_version = ""

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # helpers
    def send(self, code, body=b"", ctype="text/plain; charset=utf-8", headers=None,
             cache="no-store"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for k, v in {**SECURITY_HEADERS, **(headers or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, obj, code=200):
        self.send(code, json.dumps(obj), "application/json")

    def redirect(self, to, headers=None):
        self.send(303, "", headers={"Location": to, **(headers or {})})

    def session(self):
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return get_session(c[COOKIE].value if COOKIE in c else None)

    def body(self, limit=4096):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(min(n, limit)).decode(errors="replace")

    # routes
    def do_GET(self):
        url = urlparse(self.path)
        s = self.session()
        if url.path == "/":
            return self.send(200, APP_HTML.replace("__CSRF__", s["csrf"]) if s else LOGIN_HTML,
                             "text/html; charset=utf-8")
        if not s:
            return self.send(401, "login required")
        if url.path == "/api/dashboards":
            return self.send_json({"host": socket.gethostname(), "active": read_active(),
                                   "status": read_status(), "dashboards": list_dashboards()})
        if url.path == "/api/status":
            return self.send_json({"active": read_active(), "status": read_status()})
        if url.path.startswith("/preview/") and url.path.endswith(".png"):
            name = url.path[len("/preview/"):-len(".png")]
            if not valid_name(name):
                return self.send(404, "no such dashboard")
            try:
                with open(preview_path(name), "rb") as f:
                    png = f.read()
            except OSError:
                return self.send(404, "no example image for this dashboard")
            # static file: let the browser keep it; ?v=<mtime> busts the cache when it changes
            return self.send(200, png, "image/png", cache="private, max-age=86400")
        if url.path == "/screen.png":
            png = screen_png()
            return self.send(200, png, "image/png") if png else self.send(404, "screen not readable")
        self.send(404, "not found")

    do_HEAD = do_GET

    def do_POST(self):
        url = urlparse(self.path)
        if url.path == "/login":
            if login_locked():
                return self.send(429, LOGIN_HTML.replace("<!--MSG-->", "Too many attempts. Wait a minute."),
                                 "text/html; charset=utf-8")
            pw = parse_qs(self.body()).get("password", [""])[0]
            if check_password(pw):
                token = new_session()
                return self.redirect("/", {"Set-Cookie": f"{COOKIE}={token}; Path=/; HttpOnly; "
                                                         f"SameSite=Strict; Max-Age={SESSION_TTL}"})
            note_failure()
            time.sleep(1)
            return self.send(401, LOGIN_HTML.replace("<!--MSG-->", "Wrong password."),
                             "text/html; charset=utf-8")

        s = self.session()
        if not s or not hmac.compare_digest(self.headers.get("X-CSRF", ""), s["csrf"]):
            return self.send(403, "forbidden")
        if url.path == "/logout":
            c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
            with _sessions_lock:
                _sessions.pop(c[COOKIE].value, None)
            return self.send_json({"ok": True}, 200)
        if url.path == "/api/apply":
            try:
                name = json.loads(self.body()).get("name", "")
            except ValueError:
                name = ""
            if not valid_name(name):
                return self.send_json({"ok": False, "error": "unknown dashboard"}, 400)
            try:
                set_active(name)
            except OSError as e:
                return self.send_json({"ok": False, "error": str(e)}, 500)
            return self.send_json({"ok": True, "applied_at": time.time()})
        self.send(404, "not found")


# ------------------------------------------------------------------- pages
STYLE = """
:root{--bg:#0b0d12;--panel:#171b23;--panel2:#1f2430;--line:#2a3040;--fg:#e6e8ec;--dim:#8a93a3;
--red:#ef5350;--blue:#42a5f5;--green:#66bb6a;--amber:#ffb74d;color-scheme:dark}
*{box-sizing:border-box}
[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
button{font:inherit;border:0;border-radius:8px;padding:9px 16px;cursor:pointer;background:var(--panel2);color:var(--fg)}
button:hover:not(:disabled){filter:brightness(1.2)}
button:disabled{opacity:.45;cursor:default}
button.primary{background:var(--red);color:#fff;font-weight:600}
"""

LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>LCD Dashboards</title>
<style>""" + STYLE + """
main{max-width:340px;margin:18vh auto 0;padding:0 16px}
h1{font-size:20px;margin:0 0 4px}p{color:var(--dim);margin:0 0 20px}
input{width:100%;font:inherit;padding:10px 12px;border-radius:8px;border:1px solid var(--line);
background:var(--panel);color:var(--fg);margin-bottom:12px}
button{width:100%}.msg{color:var(--red);min-height:1.5em;margin-top:10px}
</style></head><body><main>
<h1>LCD Dashboards</h1><p>Sign in to choose what the screen shows.</p>
<form method="post" action="/login">
<input type="password" name="password" placeholder="Password" autofocus required autocomplete="current-password">
<button class="primary" type="submit">Sign in</button></form>
<div class="msg"><!--MSG--></div></main></body></html>"""

APP_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>LCD Dashboards</title>
<style>""" + STYLE + """
header{display:flex;align-items:center;gap:12px;padding:14px 20px;border-bottom:1px solid var(--line);
background:var(--panel);position:sticky;top:0;z-index:2;flex-wrap:wrap}
header h1{font-size:17px;margin:0}header .host{color:var(--dim)}
.pill{margin-left:auto;display:flex;align-items:center;gap:8px;font-size:13px;color:var(--dim)}
.dot{width:9px;height:9px;border-radius:50%;background:var(--dim)}
.dot.ok{background:var(--green)}.dot.wait{background:var(--amber)}.dot.bad{background:var(--red)}
main{max-width:1180px;margin:0 auto;padding:20px}
.stage{display:grid;grid-template-columns:1fr 1fr;gap:20px}
@media (max-width:860px){.stage{grid-template-columns:1fr}}
.pane{background:var(--panel);border-radius:12px;padding:14px}
.pane h2{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin:0 0 10px;font-weight:600}
.screen{position:relative;aspect-ratio:3/2;background:#000;border-radius:6px;overflow:hidden;
display:flex;align-items:center;justify-content:center;border:6px solid #000;box-shadow:0 0 0 1px var(--line)}
.screen img{width:100%;height:100%;object-fit:contain;image-rendering:pixelated;display:block}
.screen .note{position:absolute;color:var(--dim);font-size:13px;text-align:center;padding:20px}
.pane h2 .sub{text-transform:none;letter-spacing:0;font-weight:400;margin-left:6px}
.card .thumb{display:flex;align-items:center;justify-content:center;color:var(--dim);font-size:12px}
.meta{margin:12px 0 2px;display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.meta b{font-size:16px}.meta span{color:var(--dim);font-size:13px}
.desc{color:var(--dim);font-size:13px;min-height:1.4em;margin:0 0 12px}
.actions{display:flex;gap:10px;flex-wrap:wrap}
.msg{margin-top:12px;font-size:14px;min-height:1.4em}
.msg.ok{color:var(--green)}.msg.wait{color:var(--amber)}.msg.bad{color:var(--red)}
pre{background:#0d0f14;border-radius:8px;padding:10px;font-size:12px;overflow:auto;max-height:180px;color:var(--dim);margin:8px 0 0}
h3{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin:30px 0 12px;font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px}
@media (max-width:520px){.grid{grid-template-columns:1fr 1fr;gap:10px}main{padding:16px}}
.card{background:var(--panel);border-radius:10px;padding:8px;cursor:pointer;border:2px solid transparent;text-align:left;color:inherit}
.card:hover{border-color:var(--line)}.card.sel{border-color:var(--blue)}
.card .thumb{aspect-ratio:3/2;background:#000;border-radius:5px;overflow:hidden}
.card img{width:100%;height:100%;object-fit:contain;display:block}
.card .name{font-size:13px;margin:8px 2px 2px;display:flex;justify-content:space-between;gap:6px;word-break:break-all}
.badge{font-size:10px;font-weight:700;letter-spacing:.06em;background:var(--green);color:#08130a;border-radius:4px;padding:2px 6px;align-self:center;white-space:nowrap}
</style></head><body>
<header><h1>LCD Dashboards</h1><span class="host" id="host"></span>
<div class="pill"><span class="dot" id="dot"></span><span id="pill">…</span></div>
<button id="logout">Sign out</button></header>
<main>
<section class="stage">
 <div class="pane">
  <h2>Preview <span class="sub">example image, sample data</span></h2>
  <div class="screen" id="pvwrap"><img id="pv" alt="Example of the selected dashboard"><div class="note" id="pvnote" hidden>No example image for this dashboard yet.</div></div>
  <div class="meta"><b id="pvname">–</b><span id="pvtag"></span></div>
  <p class="desc" id="pvdesc"></p>
  <div class="actions">
   <button class="primary" id="apply" disabled>Apply to screen</button>
  </div>
  <div class="msg" id="msg"></div><div id="log"></div>
 </div>
 <div class="pane">
  <h2>On the screen now</h2>
  <div class="screen" id="livewrap"><img id="live" alt="Current screen"><div class="note" id="livenote" hidden></div></div>
  <div class="meta"><b id="livename">–</b><span id="livestate"></span></div>
  <p class="desc">Captured from the display every 5 seconds.</p>
 </div>
</section>
<h3>All dashboards</h3>
<div class="grid" id="grid"></div>
</main>
<script>
const CSRF = "__CSRF__";
const $ = id => document.getElementById(id);
let list = [], active = null, selected = null, status = {}, pending = null;

function post(url, body) {
  return fetch(url, {method: "POST", headers: {"Content-Type": "application/json", "X-CSRF": CSRF},
                     body: JSON.stringify(body || {})});
}
function ago(t) {
  const s = Math.max(0, Date.now() / 1000 - t);
  return s < 60 ? Math.round(s) + "s" : s < 3600 ? Math.round(s / 60) + "m" : Math.round(s / 3600) + "h";
}
function setMsg(text, cls, log) {
  $("msg").textContent = text; $("msg").className = "msg " + (cls || "");
  $("log").replaceChildren();
  if (log && log.length) { const p = document.createElement("pre"); p.textContent = log.join("\\n"); $("log").append(p); }
}

function previewUrl(d) {
  return "/preview/" + encodeURIComponent(d.name) + ".png?v=" + d.preview;
}

function showPreview() {
  if (!selected) return;
  const d = list.find(x => x.name === selected) || {};
  $("pvname").textContent = selected;
  $("pvtag").textContent = selected === active ? "on screen" : "";
  $("pvdesc").textContent = d.desc || "";
  $("pv").hidden = !d.preview; $("pvnote").hidden = !!d.preview;
  if (d.preview) $("pv").src = previewUrl(d);
  updateApply();
}

function updateApply() {
  $("apply").disabled = !selected || !!pending || (selected === active && status.state !== "restarting");
}

function select(name) {
  selected = name;
  document.querySelectorAll(".card").forEach(c => c.classList.toggle("sel", c.dataset.name === name));
  setMsg("");
  showPreview();
}

function renderGrid() {
  const grid = $("grid"); grid.replaceChildren();
  for (const d of list) {
    const card = document.createElement("button");
    card.className = "card" + (d.name === selected ? " sel" : ""); card.dataset.name = d.name;
    const thumb = document.createElement("div"); thumb.className = "thumb";
    if (d.preview) {
      const img = document.createElement("img"); img.loading = "lazy"; img.alt = d.name;
      img.src = previewUrl(d); thumb.append(img);
    } else {
      thumb.textContent = "no example image";
    }
    const name = document.createElement("div"); name.className = "name";
    const label = document.createElement("span"); label.textContent = d.name.replace(/\\.py$/, "");
    name.append(label);
    if (d.name === active) { const b = document.createElement("span"); b.className = "badge"; b.textContent = "ON SCREEN"; name.append(b); }
    card.append(thumb, name); card.title = d.desc || d.name;
    card.onclick = () => select(d.name);
    grid.append(card);
  }
}

function renderStatus() {
  const st = status.state || "unknown";
  const dot = $("dot"), pill = $("pill");
  const map = {running: ["ok", "Running"], starting: ["wait", "Starting"], restarting: ["bad", "Restarting after a crash"],
               reverted: ["bad", "Reverted"], stopped: ["bad", "Stopped"], unknown: ["", "Runner not reporting"]};
  const [cls, word] = map[st] || ["", st];
  dot.className = "dot " + cls;
  pill.textContent = status.script ? `${word} · ${status.script.replace(/\\.py$/, "")} · ${ago(status.since)}` : word;
  $("livename").textContent = status.script || "–";
  $("livestate").textContent = st === "running" ? "running for " + ago(status.since) : st;
}

async function refreshStatus() {
  try {
    const r = await fetch("/api/status"); if (r.status === 401) return location.reload();
    const j = await r.json();
    const prevActive = active;
    active = j.active; status = j.status || {};
    if (active !== prevActive) renderGrid();
    renderStatus(); updateApply();
    if (pending) checkPending();
  } catch (e) { $("pill").textContent = "Server not reachable"; $("dot").className = "dot bad"; }
}

function checkPending() {
  const err = status.last_error;
  if (err && err.script === pending.name && err.at >= pending.at) {
    setMsg(`${pending.name} stopped after ${err.after}s (exit code ${err.code}). ` +
           (status.good ? `The screen went back to ${status.good}.` : "It will be retried."), "bad", err.log);
    pending = null; updateApply(); return;
  }
  if (status.script === pending.name && status.state === "running") {
    setMsg(`${pending.name} is on the screen and running.`, "ok"); pending = null; updateApply(); return;
  }
  if (Date.now() / 1000 - pending.at > 60) {
    setMsg("No confirmation from the screen after a minute. Is the LCD service running?", "bad");
    pending = null; updateApply(); return;
  }
  setMsg(status.script === pending.name ? "Started. Checking it stays up for 15 seconds…" : "Switching…", "wait");
}

$("apply").onclick = async () => {
  if (!selected) return;
  $("apply").disabled = true;
  const r = await post("/api/apply", {name: selected});
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.ok) { setMsg("Could not apply: " + (j.error || r.status), "bad"); updateApply(); return; }
  pending = {name: selected, at: j.applied_at};
  active = selected; renderGrid(); checkPending(); updateApply();
};
$("logout").onclick = async () => { await post("/logout"); location.reload(); };

let liveFailed = false;
function refreshLive() {
  if (liveFailed || document.hidden) return;      // no captures for background tabs
  const img = new Image();
  img.onload = () => { $("live").src = img.src; $("live").hidden = false; $("livenote").hidden = true; };
  img.onerror = () => {
    liveFailed = true; $("live").hidden = true;
    $("livenote").textContent = "Live view unavailable: the web UI can't read the framebuffer.";
    $("livenote").hidden = false;
  };
  img.src = "/screen.png?t=" + Date.now();
}

(async () => {
  const r = await fetch("/api/dashboards"); if (r.status === 401) return location.reload();
  const j = await r.json();
  list = j.dashboards; active = j.active; status = j.status || {};
  $("host").textContent = j.host;
  renderGrid(); renderStatus();
  select(active && list.some(d => d.name === active) ? active : (list[0] || {}).name);
  refreshLive();
  setInterval(refreshStatus, 2000);
  setInterval(refreshLive, 5000);
  document.addEventListener("visibilitychange", refreshLive);
})();
</script></body></html>"""


def main():
    if sys.argv[1:] == ["set-password"]:
        return set_password()
    if len(sys.argv) > 1:
        sys.exit(__doc__)
    if not os.path.exists(PASSWORD_FILE):
        sys.exit(f"No password set. Run: {sys.argv[0]} set-password")
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    server.daemon_threads = True
    print(f"LCD web UI on http://{BIND}:{PORT}  (dashboards: {DASH_DIR})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
