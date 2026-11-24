#!/usr/bin/env python3
"""Run the active LCD dashboard and switch when the web UI changes it.

The LCD service runs this instead of a dashboard script directly. It reads
the chosen script name from $LCD_STATE_DIR/active, runs that script as a
child process and restarts it if it crashes. When the active file changes,
it switches scripts. If a newly chosen script exits within GRACE seconds,
the previous working script is restored and the error is reported in
$LCD_STATE_DIR/status.json for the web UI.

Environment:
  LCD_DASH_DIR   folder with the dashboard scripts (default: parent of this file)
  LCD_STATE_DIR  shared state folder (default: /var/lib/lcd-dashboard)
  LCD_DEFAULT    script to run when nothing has been chosen yet
"""
import collections
import json
import os
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DASH_DIR = os.path.abspath(os.environ.get("LCD_DASH_DIR", os.path.join(HERE, "..")))
STATE_DIR = os.environ.get("LCD_STATE_DIR", "/var/lib/lcd-dashboard")
DEFAULT = os.environ.get("LCD_DEFAULT", "lcd_180.py")
ACTIVE = os.path.join(STATE_DIR, "active")
STATUS = os.path.join(STATE_DIR, "status.json")

GRACE = 15        # seconds a script must stay up to count as working
POLL = 1          # seconds between checks
BACKOFF = 5       # seconds before restarting a script that crashed later on


def valid(name):
    return (bool(name) and "/" not in name and not name.startswith(".")
            and name.endswith(".py") and os.path.isfile(os.path.join(DASH_DIR, name)))


def write_atomic(path, data):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        f.write(data)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def read_active():
    try:
        with open(ACTIVE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


class Child:
    """One running dashboard script and the tail of its output."""
    def __init__(self, name):
        self.name = name
        self.started = time.time()
        self.log = collections.deque(maxlen=25)
        self.proc = subprocess.Popen(
            [sys.executable, "-u", os.path.join(DASH_DIR, name)],
            cwd=DASH_DIR, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", start_new_session=True)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            self.log.append(line.rstrip())

    def exited(self):
        return self.proc.poll() is not None

    def stop(self):
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait()
            except ProcessLookupError:
                pass


def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    stopping = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopping.set())

    child = None
    good = None             # last script that stayed up for GRACE seconds
    state = "starting"
    last_error = None
    restart_at = 0
    last_status = None

    if not valid(read_active() or ""):
        write_atomic(ACTIVE, DEFAULT + "\n")

    while not stopping.is_set():
        want = read_active()
        if not valid(want or ""):
            want = good or DEFAULT

        # switch to a newly chosen script
        if child is None or want != child.name:
            if child is None and time.time() < restart_at and want == (last_error or {}).get("script"):
                pass        # waiting out the back-off before restarting
            else:
                if child: child.stop()
                child = Child(want)
                state = "starting"

        elif child.exited():
            code = child.proc.returncode
            ran = time.time() - child.started
            failed = child.name
            last_error = {"script": failed, "code": code, "after": round(ran, 1),
                          "at": time.time(), "log": list(child.log)}
            child = None
            if ran < GRACE and good and good != failed:
                write_atomic(ACTIVE, good + "\n")      # roll back
                state = "reverted"
            else:
                state = "restarting"
                restart_at = time.time() + BACKOFF

        elif state == "starting" and time.time() - child.started >= GRACE:
            state = "running"
            good = child.name

        status = {"script": child.name if child else None, "state": state,
                  "since": child.started if child else None,
                  "pid": child.proc.pid if child else None,
                  "good": good, "last_error": last_error}
        if status != last_status:
            write_atomic(STATUS, json.dumps(status))
            last_status = status
        stopping.wait(POLL)

    if child: child.stop()
    write_atomic(STATUS, json.dumps({"script": None, "state": "stopped", "since": None,
                                     "pid": None, "good": good, "last_error": last_error}))


if __name__ == "__main__":
    main()
