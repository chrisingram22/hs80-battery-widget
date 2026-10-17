"""HS80 Max battery tray widget — entry point. Run with pythonw.exe (no console).

- single instance (named mutex)
- rotating log in %APPDATA%\\HS80Widget\\widget.log
- config.json next to it
- Tk on the main thread (floating window), pystray detached, poller on its own thread
"""
from __future__ import annotations

import ctypes
import logging
import logging.handlers
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import config as C                      # noqa: E402
from hs80.poller import Poller          # noqa: E402
from widget.notify import Alerts        # noqa: E402

MUTEX_NAME = "Local\\HS80BatteryWidget"
ERROR_ALREADY_EXISTS = 183
_mutex_handle = None


def setup_logging() -> None:
    os.makedirs(C.APP_DIR, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(C.LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None and sys.stderr.isatty():
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)


def single_instance() -> bool:
    """True if this is the only instance. The mutex handle stays open for the process lifetime."""
    global _mutex_handle
    k32 = ctypes.windll.kernel32
    handle = k32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return True
    if k32.GetLastError() == ERROR_ALREADY_EXISTS:
        return False
    _mutex_handle = handle
    return True


def set_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)      # per-monitor v1; crisp Tk text
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    setup_logging()
    log = logging.getLogger("hs80.main")
    if "--test-toast" in argv:                  # packaging smoke test: prove notifications work
        import time
        from widget.notify import win11_send
        win11_send("HS80 Battery", "Test notification from " + ("the exe" if getattr(sys, "frozen", False) else "Python"))
        time.sleep(3)
        return 0
    if "--version" in argv:
        from hs80 import __version__
        print(f"HS80 Battery Widget {__version__}")
        return 0
    if not single_instance():
        log.info("another instance is running; exiting")
        return 0
    set_dpi_awareness()
    import tkinter as tk
    from widget.app import App

    cfg = C.load()
    try:
        C.save(cfg)          # materialise defaults (e.g. product_ids) so the file is there to edit
    except OSError as e:
        log.warning("could not write config: %s", e)
    log.info("starting %s: interval %d s, thresholds %s, alerts %s, float %s",
             "exe" if getattr(sys, "frozen", False) else "python", cfg.interval_s, cfg.thresholds, cfg.alerts, cfg.show_float)
    try:
        import autostart
        autostart.refresh_if_stale()
    except Exception as e:
        log.warning("autostart refresh failed: %s", e)
    alerts = Alerts(cfg.thresholds, enabled=cfg.alerts, disconnect_alert=cfg.disconnect_alert, fired_at=cfg.last_alerts)
    poller = Poller(cfg.interval_s, vid=cfg.vendor_id, pids=cfg.product_ids)
    log.info("looking for %s", ", ".join(f"{cfg.vendor_id:04X}:{p:04X}" for p in cfg.product_ids))
    root = tk.Tk()
    app = App(root, cfg, poller, alerts)
    try:
        app.run()
    except Exception:
        log.exception("fatal")
        return 1
    log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
