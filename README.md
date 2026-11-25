# Pi-hole LCD dashboards

Live Pi-hole stats on a 3.5" 480×320 SPI screen (the common ILI9486 "LCD35" boards
that plug onto the Pi's GPIO header), with a small web page to pick from 48 designs
or draw your own in a built-in screen editor.

## Install

On the Pi, with Pi-hole already installed and the screen plugged in:

```sh
sudo apt install -y git
git clone https://github.com/ErnestAC/dashboards.git
cd dashboards
sudo ./install.sh
```

The installer:

1. installs what the dashboards need (Python Pillow and psutil, DejaVu and Roboto fonts);
2. copies the project to `/opt/lcd-dashboards`;
3. looks for the screen. If its driver isn't set up yet, it offers to add it to
   `/boot/firmware/config.txt` (a backup is kept) and tells you to reboot;
4. starts two services and asks you to choose a password for the web page.

Then open `http://<your-pi>:8911`, sign in, pick a dashboard and press **Apply**.

### If the picture is sideways or upside down

```sh
sudo ./install.sh --rotate 270     # or 0, 90 (the default), 180
sudo reboot
```

### If your screen needs a different driver

Pass the exact overlay line from your screen's maker:

```sh
sudo ./install.sh --overlay "dtoverlay=tft35a:rotate=90"
```

If you've already set the screen up yourself (for example with the maker's
`LCD35-show` script), the installer finds it and leaves the boot config alone.

## Design your own screens

On the web page, click **+ New screen** under *My screens*. The editor opens with a
starter layout you can change or clear:

- **Add** text, boxes, bars, rings, a 24-hour chart and lists with the buttons on the left.
- **Drag** to move, drag a handle to resize, arrow keys nudge (Shift for 10 px).
- **Text** can include live values: pick one from *Insert a live value…* or type
  `{time}`, `{blocked_pct}`, `{queries}`, `{temp}`, `{last_blocked}` and so on.
- **Ctrl+Z** undoes, **Ctrl+D** duplicates, **Delete** removes, **Ctrl+S** saves.

What you see is what the LCD shows: the editor draws at the screen's 480×320 with
the Pi's own fonts. The numbers in the editor are sample data; the LCD shows live ones.

**Save** keeps the screen in *My screens*; **Save & show on screen** also puts it on
the LCD. Select a saved screen on the main page to **Edit** or **Delete** it (apply a
different dashboard before deleting the one that's showing). Screens are stored as
small JSON files in `/var/lib/lcd-dashboard/screens/` on the Pi: back that folder up
to keep them, or copy files into it to share screens between Pis.

## Update

```sh
cd dashboards && git pull && sudo ./install.sh
```

Your chosen dashboard, password and saved screens are kept.

## Other options

| Option | What it does |
|---|---|
| `--port N` | web page port (default 8911) |
| `--default FILE` | dashboard shown until you pick one (default `lcd_180.py`) |
| `--no-display` | never touch the boot config |
| `--unit NAME` | reuse an LCD service you already have instead of adding `lcd-dashboard.service` |
| `--yes` | don't ask questions |

Change the web password: `sudo -u lcdweb LCD_STATE_DIR=/var/lib/lcd-dashboard python3 /opt/lcd-dashboards/webui/lcd_webui.py set-password`

Logs: `journalctl -u lcd-dashboard -u lcd-webui -f`

## Uninstall

```sh
sudo /opt/lcd-dashboards/uninstall.sh           # remove the services, keep your settings and screens
sudo /opt/lcd-dashboards/uninstall.sh --purge   # also delete the files, settings, saved screens and users
```

Pi-hole itself is never changed: the screen only reads its query database.

## How it fits together

| Part | Role |
|---|---|
| `lcd_*.py` | the built-in dashboards, one script each |
| `webui/lcd_runner.py` | runs the chosen dashboard (`lcd-dashboard.service`), falls back if it crashes |
| `webui/lcd_webui.py` | the web page (`lcd-webui.service`) |
| `webui/editor.html` | the screen editor |
| `webui/screens.py` | the saved-screen format and its checks, used by the web page and `lcd_screen.py` |
| `webui/lcd_screen.py` | draws a saved screen on the LCD |
| `/var/lib/lcd-dashboard/` | chosen dashboard, status, password hash, `screens/` |

## Making previews

The web page shows the `.png` next to each script. Regenerate them on any machine
(no screen or Pi-hole needed) with sample data:

```sh
tools/make_example.py --all
```

Saved screens don't need this: the editor saves its own picture with each one.
