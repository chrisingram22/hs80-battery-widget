"""Background poller: owns the device, publishes BatteryState, never lets the UI touch HID.

State machine (see DEV_PLAN.md):
  OK         valid level in the last read
  ASLEEP     receiver present, headset gives no valid reply
  NO_DONGLE  no control collection enumerated
  ERROR      open/write/read raised while the receiver is still present; the handle is
             dropped at once and reopened on the next poll, with backoff 15 s → 5 min
Any failure drops the handle. If the receiver is no longer enumerable the state is
NO_DONGLE (not ERROR) and the receiver is looked for every 15 s until it is back.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Callable

from . import device as D
from .state import BatteryState, Status

log = logging.getLogger("hs80.poller")

MIN_POLL_GAP_S = 2.0        # hs80-max-control RateGate
RECOVERY_POLL_S = 15.0      # how often to look for the receiver / retry after an error
BACKOFF_MAX_S = 300.0

StateCallback = Callable[[BatteryState], None]


class Poller:
    def __init__(self, interval_s: float = 60.0, *,
                 on_change: StateCallback | None = None,
                 on_poll: StateCallback | None = None,
                 open_device: Callable[[], D.Hs80Device] | None = None,
                 receiver_present: Callable[[], bool] | None = None,
                 vid: int = D.P.VID, pids=(D.P.PID,),
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 now: Callable[[], datetime] = datetime.now):
        self.interval_s = float(interval_s)
        self.on_change = on_change
        self.on_poll = on_poll
        pids = tuple(pids)
        self._open_device = open_device or (lambda: D.Hs80Device.open(vid=vid, pids=pids))
        self._receiver_present = receiver_present or (lambda: D.find_path(vid, pids) is not None)
        self._clock = clock
        self._sleep = sleep
        self._now = now

        self._dev: D.Hs80Device | None = None
        self._state = BatteryState()
        self._lock = threading.Lock()
        self._errors = 0
        self._last_poll = -1e9
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.next_delay_s = self.interval_s

    # ---- public -----------------------------------------------------------------
    @property
    def state(self) -> BatteryState:
        with self._lock:
            return self._state

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hs80-poller", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)
        self._close_device()

    def refresh_now(self) -> None:
        """Ask the loop to poll as soon as the 2 s rate gate allows."""
        self._wake.set()

    def set_interval(self, seconds: float) -> None:
        self.interval_s = float(seconds)
        self._wake.set()

    def poll_once(self) -> BatteryState:
        """One poll, synchronous. Safe to call from the loop thread or the CLI (not both)."""
        gap = MIN_POLL_GAP_S - (self._clock() - self._last_poll)
        if gap > 0:
            self._sleep(gap)
        t0 = self._clock()
        prev = self.state
        new = self._poll(prev)
        self._last_poll = self._clock()
        new = new.with_(read_ms=int((self._last_poll - t0) * 1000), polls=prev.polls + 1)
        with self._lock:
            self._state = new
        self.next_delay_s = self._delay_after(new)
        if self.on_poll:
            self.on_poll(new)
        if self.on_change and new.changed_from(prev):
            self.on_change(new)
        return new

    # ---- internals -----------------------------------------------------------------
    def _poll(self, prev: BatteryState) -> BatteryState:
        try:
            if self._dev is None:
                self._dev = self._open_device()
            pct, _ = self._dev.read_battery()
            if pct is None:
                self._errors = 0
                return prev.with_(status=Status.ASLEEP, percent=None, charging=None, charge_raw=None, error=None)
            charging, charge_raw = self._dev.read_charge_raw()
            self._errors = 0
            return prev.with_(status=Status.OK, percent=pct, charging=charging, charge_raw=charge_raw,
                              last_percent=pct, last_ok=self._now(), error=None)
        except D.DongleNotFound:
            self._close_device()
            self._errors = 0
            return prev.with_(status=Status.NO_DONGLE, percent=None, charging=None, charge_raw=None, error=None)
        except Exception as e:   # DeviceError or anything hidapi throws
            self._close_device()                 # always drop the handle; reopen on the next poll
            if not self._present():
                log.info("receiver gone (%s)", e)
                self._errors = 0
                return prev.with_(status=Status.NO_DONGLE, percent=None, charging=None, charge_raw=None, error=None)
            self._errors += 1
            log.warning("poll error %d: %s", self._errors, e)
            return prev.with_(status=Status.ERROR, percent=None, charging=None, charge_raw=None, error=str(e))

    def _present(self) -> bool:
        try:
            return bool(self._receiver_present())
        except Exception as e:
            log.warning("enumeration failed: %s", e)
            return True

    def _delay_after(self, st: BatteryState) -> float:
        if st.status is Status.NO_DONGLE:
            return min(self.interval_s, RECOVERY_POLL_S)
        if st.status is Status.ERROR:
            return min(RECOVERY_POLL_S * (2 ** max(0, self._errors - 1)), BACKOFF_MAX_S, max(self.interval_s, RECOVERY_POLL_S))
        return self.interval_s

    def _close_device(self) -> None:
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:
                pass
            self._dev = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                log.exception("unexpected poller failure")
                self.next_delay_s = self.interval_s
            self._wake.wait(self.next_delay_s)
            self._wake.clear()
