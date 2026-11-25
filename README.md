# Pi-hole LCD dashboards

Live Pi-hole stats on a 3.5" 480×320 SPI screen (the common ILI9486 "LCD35" boards
that plug onto the Pi's GPIO header), with a small web page to pick from 40+ designs.

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

## Update

```sh
cd dashboards && git pull && sudo ./install.sh
```

Your chosen dashboard and password are kept.

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
sudo /opt/lcd-dashboards/uninstall.sh           # remove the services
sudo /opt/lcd-dashboards/uninstall.sh --purge   # also the files, settings and users
```

Pi-hole itself is never changed: the screen only reads its query database.

## Making previews

The web page shows the `.png` next to each script. Regenerate them on any machine
(no screen or Pi-hole needed) with sample data:

```sh
tools/make_example.py --all
```
