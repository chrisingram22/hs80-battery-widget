"""Tray icon pieces: tooltip text and the pystray menu, built against the App controller."""
from __future__ import annotations

import autostart
import config as C
from hs80.state import BatteryState, Status
from pystray import Menu, MenuItem as Item

INTERVAL_LABELS = {30: "30 seconds", 60: "1 minute", 120: "2 minutes", 300: "5 minutes"}


def tooltip(st: BatteryState) -> str:
    when = st.last_ok.strftime("%H:%M") if st.last_ok else "never"
    if st.status is Status.OK:
        chg = "charging" if st.charging else "on battery"
        return f"HS80 Max: {st.percent:.0f}% · {chg} · updated {when}"
    if st.status is Status.ASLEEP:
        last = f" · last {st.last_percent:.0f}% at {when}" if st.last_percent is not None else ""
        return f"HS80 Max: headset off or asleep{last}"
    if st.status is Status.NO_DONGLE:
        return "HS80 Max: receiver not found"
    return f"HS80 Max: error ({st.error or '?'}) · retrying"


def build_tray_menu(app) -> Menu:
    return Menu(
        Item(lambda item: tooltip(app.state), None, enabled=False),
        Menu.SEPARATOR,
        Item("Refresh now", app.refresh),
        Item("Poll interval", Menu(*[
            Item(INTERVAL_LABELS[s], (lambda s: lambda icon, item: app.set_interval(s))(s),
                 checked=(lambda s: lambda item: app.cfg.interval_s == s)(s), radio=True)
            for s in C.INTERVAL_CHOICES])),
        Item("Low-battery alerts", app.toggle_alerts, checked=lambda item: app.cfg.alerts),
        Item("Start with Windows", app.toggle_autostart, checked=lambda item: autostart.is_enabled()),
        Menu.SEPARATOR,
        Item("Show floating widget", app.toggle_float, checked=lambda item: app.float.visible),
        Item("Floating widget click-through", app.toggle_click_through, checked=lambda item: app.cfg.click_through),
        Menu.SEPARATOR,
        Item("Open log", app.open_log),
        Item("Quit", app.quit),
    )
