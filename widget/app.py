"""App controller: owns config, poller, alerts, the tray icon and the floating window.

Threading: Tk owns the main thread; pystray runs detached on its own thread; the poller
thread pushes states into a queue that Tk drains every 200 ms. Both menus call the same
action methods here, so tray and floating window never disagree.
"""
from __future__ import annotations

import ctypes
import logging
import os
import queue
import threading
import time
import tkinter as tk
from datetime import datetime

import pystray

import autostart
import config as C
from hs80.poller import Poller
from hs80.state import BatteryState
from widget import icon as I
from widget.float import FloatWindow
from widget.notify import Alerts
from widget.tray import INTERVAL_LABELS, build_tray_menu, tooltip

log = logging.getLogger("hs80.app")

DRAIN_MS = 200
HEARTBEAT_S = 3600


class App:
    def __init__(self, root: tk.Tk, cfg: C.Config, poller: Poller, alerts: Alerts):
        self.root = root
        self.cfg = cfg
        self.poller = poller
        self.alerts = alerts
        self._q: queue.Queue[BatteryState] = queue.Queue()
        self._lock = threading.Lock()
        self._shown = BatteryState()
        self._stopping = False
        self._t0 = time.monotonic()
        self._last_beat = self._t0
        self._ok_polls = 0
        self._all_polls = 0
        alerts._on_fire = self._remember_alert

        poller.on_poll = self._q.put            # poller thread -> queue
        poller.on_change = None
        self.tray = pystray.Icon("hs80-battery", I.render(self._shown), tooltip(self._shown), build_tray_menu(self))
        self.float = FloatWindow(root, self)

    # ---- lifecycle ------------------------------------------------------------------
    def run(self) -> None:
        self.tray.run_detached()
        self.poller.start()
        if self.cfg.show_float:
            self.float.show()
        self.root.after(DRAIN_MS, self._drain)
        try:
            self.root.mainloop()
        finally:
            self.poller.stop()
            try:
                self.tray.stop()
            except Exception:
                pass

    def quit(self, *_) -> None:
        if self._stopping:
            return
        self._stopping = True
        log.info("quit")
        self.root.after(0, self.root.quit)

    # ---- state flow (Tk thread) ---------------------------------------------------
    def _drain(self) -> None:
        latest = None
        try:
            while True:
                st = self._q.get_nowait()
                self.alerts.on_state(st)
                latest = st
                self._all_polls += 1
                self._ok_polls += st.status.value == "ok"
        except queue.Empty:
            pass
        if latest is not None:
            self._apply(latest)
            self._heartbeat()
        if not self._stopping:
            self.root.after(DRAIN_MS, self._drain)

    def _apply(self, st: BatteryState) -> None:
        changed = st.changed_from(self._shown)
        self._shown = st
        self.tray.title = tooltip(st)[:127]
        if changed:
            log.info("state -> %s (chg_raw=%s, %d ms)", st.summary(), st.charge_raw, st.read_ms)
            self.tray.icon = I.render(st)
            self.float.update(st)

    @property
    def state(self) -> BatteryState:
        return self._shown

    def _heartbeat(self) -> None:
        now = time.monotonic()
        if now - self._last_beat < HEARTBEAT_S:
            return
        self._last_beat = now
        up = int(now - self._t0)
        log.info("heartbeat: up %dh%02dm polls=%d ok=%d rss=%.0f MB state=%s",
                 up // 3600, up % 3600 // 60, self._all_polls, self._ok_polls, _rss_mb(), self._shown.summary())

    def _remember_alert(self, threshold: int, when: datetime) -> None:
        self._save(self.cfg.with_(last_alerts={**self.cfg.last_alerts, str(threshold): when.isoformat(timespec="seconds")}))

    # ---- actions (called from either menu) ----------------------------------------
    def refresh(self, *_) -> None:
        self.poller.refresh_now()

    def set_interval(self, seconds: int) -> None:
        self._save(self.cfg.with_(interval_s=int(seconds)))
        self.poller.set_interval(seconds)
        log.info("interval -> %d s", seconds)

    def toggle_alerts(self, *_) -> None:
        self._save(self.cfg.with_(alerts=not self.cfg.alerts))
        self.alerts.configure(enabled=self.cfg.alerts)
        log.info("alerts -> %s", self.cfg.alerts)

    def toggle_autostart(self, *_) -> None:
        try:
            autostart.toggle()
        except Exception as e:
            log.error("autostart toggle failed: %s", e)
            self.tray.notify(f"Could not change the Startup shortcut: {e}", "HS80 Battery")

    def toggle_float(self, *_) -> None:
        show = not self.float.visible
        self.root.after(0, self.float.show if show else self.float.hide)
        self._save(self.cfg.with_(show_float=show))
        log.info("floating window -> %s", "shown" if show else "hidden")

    def toggle_click_through(self, *_) -> None:
        on = not self.cfg.click_through
        self._save(self.cfg.with_(click_through=on))
        self.root.after(0, lambda: self.float.set_click_through(on))
        log.info("click-through -> %s", on)

    def save_float_pos(self, pos: tuple[int, int]) -> None:
        self._save(self.cfg.with_(float_pos=(int(pos[0]), int(pos[1]))))

    def open_log(self, *_) -> None:
        if os.path.isfile(C.LOG_PATH):
            os.startfile(C.LOG_PATH)

    def _save(self, cfg: C.Config) -> None:
        with self._lock:
            self.cfg = cfg
            try:
                C.save(cfg)
            except OSError as e:
                log.error("config save failed: %s", e)

    # ---- tk menu for the floating window -----------------------------------------------
    def build_tk_menu(self, parent: tk.Misc) -> tk.Menu:
        m = tk.Menu(parent, tearoff=0)
        m.add_command(label=tooltip(self.state), state="disabled")
        m.add_separator()
        m.add_command(label="Refresh now", command=self.refresh)
        sub = tk.Menu(m, tearoff=0)
        var = tk.IntVar(master=parent, value=self.cfg.interval_s)
        for s in C.INTERVAL_CHOICES:
            sub.add_radiobutton(label=INTERVAL_LABELS[s], variable=var, value=s,
                                command=lambda s=s: self.set_interval(s))
        m.add_cascade(label="Poll interval", menu=sub)
        alerts_var = tk.BooleanVar(master=parent, value=self.cfg.alerts)
        m.add_checkbutton(label="Low-battery alerts", variable=alerts_var, command=self.toggle_alerts)
        auto_var = tk.BooleanVar(master=parent, value=autostart.is_enabled())
        m.add_checkbutton(label="Start with Windows", variable=auto_var, command=self.toggle_autostart)
        ct_var = tk.BooleanVar(master=parent, value=self.cfg.click_through)
        m.add_checkbutton(label="Click-through (manage from tray)", variable=ct_var, command=self.toggle_click_through)
        m.add_separator()
        m.add_command(label="Hide floating widget", command=self.toggle_float)
        m.add_command(label="Open log", command=self.open_log)
        m.add_command(label="Quit", command=self.quit)
        m._vars = (var, alerts_var, auto_var, ct_var)   # keep alive while posted
        return m



def _rss_mb() -> float:
    """Working set of this process in MB (psapi, no psutil dependency)."""
    class PMC(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    try:
        pmc = PMC(); pmc.cb = ctypes.sizeof(PMC)
        ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return pmc.WorkingSetSize / 1048576
    except Exception:
        return -1.0
