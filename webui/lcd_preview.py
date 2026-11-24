#!/usr/bin/env python3
"""Render one dashboard script to a PNG without touching the screen.

usage: lcd_preview.py <dashboard.py> <out.png>

The script runs with real data (Pi-hole DB, CPU, temperature). Writes to
/dev/fb* are intercepted: scripts that start their display loop on import
are stopped at their first frame, then draw_screen(get_stats()) is called
to produce the image.
"""
import builtins
import importlib.util
import io
import os
import sys

sys.dont_write_bytecode = True


class _StopRender(Exception):
    pass


class _FramebufferSink(io.BytesIO):
    """Stands in for /dev/fb*: swallows the frame, then stops the script."""
    def close(self):
        if not getattr(self, "_stopped", False):
            self._stopped = True
            raise _StopRender

    def __exit__(self, *exc):
        self.close()


def _open(path, *args, **kwargs):
    if isinstance(path, str) and path.startswith("/dev/fb"):
        return _FramebufferSink()
    return builtins.open(path, *args, **kwargs)


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    script, out = sys.argv[1], sys.argv[2]
    try:
        os.nice(10)
    except OSError:
        pass

    spec = importlib.util.spec_from_file_location("dashboard", script)
    module = importlib.util.module_from_spec(spec)
    module.open = _open                       # the script's open() for /dev/fb*
    try:
        spec.loader.exec_module(module)
    except _StopRender:
        pass                                  # module-level loop reached write_fb

    img = module.draw_screen(module.get_stats())
    img.save(out, "PNG")


if __name__ == "__main__":
    main()
