"""Low-battery and disconnect toasts with per-threshold debounce.

Alerts is pure logic with an injectable `send(title, body)`; the real sender uses
win11toast.notify (non-blocking).
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Callable, Iterable

from hs80.state import BatteryState, Status

log = logging.getLogger("hs80.notify")

RESET_MARGIN = 5     # a threshold re-arms once the level is this far above it
REPEAT_AFTER_S = 2 * 3600   # a restart does not repeat a toast fired this recently at the same threshold


def win11_send(title: str, body: str) -> None:
    def _go():
        try:
            from win11toast import notify
            notify(title, body, app_id="HS80 Battery")
        except Exception as e:   # toasts must never take the widget down
            log.warning("toast failed: %s", e)
    threading.Thread(target=_go, name="hs80-toast", daemon=True).start()


class Alerts:
    def __init__(self, thresholds: Iterable[int] = (30, 10), *, enabled: bool = True,
                 disconnect_alert: bool = False, send: Callable[[str, str], None] = win11_send,
                 fired_at: dict | None = None, on_fire: Callable[[int, datetime], None] | None = None,
                 now: Callable[[], datetime] = datetime.now, repeat_after_s: float = REPEAT_AFTER_S):
        self.thresholds = tuple(sorted(set(int(t) for t in thresholds), reverse=True))
        self.enabled = enabled
        self.disconnect_alert = disconnect_alert
        self._send = send
        self._fired: set[int] = set()
        self._was_ok: bool | None = None
        self.sent: list[tuple[str, str]] = []
        self._on_fire = on_fire
        self._now = now
        # pre-arm thresholds that toasted recently (persisted by the app) so a restart stays quiet
        for t, ts in (fired_at or {}).items():
            try:
                t = int(t)
                when = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts))
            except (TypeError, ValueError):
                continue
            if t in self.thresholds and now() - when < timedelta(seconds=repeat_after_s):
                self._fired.add(t)

    def configure(self, thresholds: Iterable[int] | None = None, enabled: bool | None = None,
                  disconnect_alert: bool | None = None) -> None:
        if thresholds is not None:
            self.thresholds = tuple(sorted(set(int(t) for t in thresholds), reverse=True))
            self._fired &= set(self.thresholds)
        if enabled is not None:
            self.enabled = enabled
        if disconnect_alert is not None:
            self.disconnect_alert = disconnect_alert

    def _emit(self, title: str, body: str) -> None:
        self.sent.append((title, body))
        log.info("toast: %s - %s", title, body)
        self._send(title, body)

    def on_state(self, st: BatteryState) -> None:
        ok = st.status is Status.OK
        if self.disconnect_alert and self._was_ok and st.status is Status.ASLEEP:
            self._emit("HS80 Max disconnected", "Headset is off, asleep or out of range.")
        self._was_ok = ok
        if not ok or st.percent is None:
            return
        if st.charging:
            self._fired.clear()
            return
        for t in self.thresholds:
            if st.percent > t + RESET_MARGIN:
                self._fired.discard(t)
        if not self.enabled:
            return
        # fire only the lowest crossed threshold not yet fired
        crossed = [t for t in self.thresholds if st.percent <= t and t not in self._fired]
        if crossed:
            t = min(crossed)
            # mark every crossed threshold so dropping past two at once gives one toast
            self._fired.update(crossed)
            if self._on_fire:
                self._on_fire(t, self._now())
            urgent = t == min(self.thresholds)
            self._emit("HS80 Max battery " + ("critical" if urgent else "low"),
                       f"{st.percent:.0f}% left. Plug the headset in{' now' if urgent else ' soon'}.")
