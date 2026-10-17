"""Hs80Device: talks to the receiver's vendor HID collection.

The HID transport is injectable so the poller and this class can be tested offline with
replayed replies (see tests/). The real transport is hidapi (cython-hidapi, module `hid`).

Rules carried over from HeadsetControl / hs80-max-control / HaloBattery and Phase 0:
  * select the collection by usage page 0xFF42 / usage 0x0001, never by interface index
  * every read is timed (500 ms); nothing can block forever
  * at least 50 ms between writes
  * a battery value of 0 or > 1000 is "no answer", retried, never shown as a level
  * bare battery request first; the three-write wake handshake only as a fallback
  * never send a set (cmd 0x01): that would take control away from iCUE
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Protocol

from . import protocol as P
from .protocol import Reply, hexdump

log = logging.getLogger("hs80.device")

READ_TIMEOUT_MS = 500
FLUSH_TIMEOUT_MS = 30
MIN_WRITE_GAP_S = 0.05
BATTERY_ATTEMPTS = 3
REPLY_WINDOW_S = 1.5


class DeviceError(RuntimeError):
    """An HID open/write/read failed."""


class DongleNotFound(DeviceError):
    """No FF42/0001 collection of 1B1C:0A97 is present."""


class Transport(Protocol):
    def write(self, buf: bytes) -> int: ...
    def read(self, size: int, timeout_ms: int) -> bytes: ...
    def close(self) -> None: ...


class HidapiTransport:
    """cython-hidapi backed transport."""

    def __init__(self, path: bytes):
        import hid  # imported here so the package loads without hidapi for offline tests
        self._dev = hid.device()
        try:
            self._dev.open_path(path)
        except (OSError, IOError, ValueError) as e:
            raise DeviceError(f"open failed: {e}") from e
        self._dev.set_nonblocking(0)

    def write(self, buf: bytes) -> int:
        try:
            n = self._dev.write(buf)
        except (OSError, IOError, ValueError) as e:
            raise DeviceError(f"write failed: {e}") from e
        if n is None or n < 0:
            raise DeviceError(f"write failed (returned {n})")
        return n

    def read(self, size: int, timeout_ms: int) -> bytes:
        try:
            r = self._dev.read(size, timeout_ms)
        except (OSError, IOError, ValueError) as e:
            raise DeviceError(f"read failed: {e}") from e
        return bytes(r) if r else b""

    def close(self) -> None:
        try:
            self._dev.close()
        except Exception:
            pass


def find_device(vid: int = P.VID, pids=(P.PID,)) -> dict | None:
    """hidapi device-info dict of the control collection (usage FF42:0001) of the first
    receiver in `pids` order, or None when none is present. Never selects by interface index."""
    import hid
    pids = tuple(pids)
    found = [d for d in hid.enumerate(vid, 0)
             if d.get("product_id") in pids and d.get("usage_page") == P.USAGE_PAGE and d.get("usage") == P.USAGE]
    found.sort(key=lambda d: pids.index(d["product_id"]))
    return found[0] if found else None


def find_path(vid: int = P.VID, pids=(P.PID,)) -> bytes | None:
    d = find_device(vid, pids)
    return d["path"] if d else None


class Hs80Device:
    def __init__(self, transport: Transport, *, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self._t = transport
        self._sleep = sleep
        self._clock = clock
        self._last_write = -1.0
        self.closed = False

    # ---- construction -------------------------------------------------------------
    @classmethod
    def open(cls, path: bytes | None = None, vid: int = P.VID, pids=(P.PID,)) -> "Hs80Device":
        """Open the real receiver. Raises DongleNotFound / DeviceError."""
        if path is None:
            info = find_device(vid, pids)
            if info is None:
                ids = ", ".join(f"{vid:04X}:{p:04X}" for p in pids)
                raise DongleNotFound(f"receiver not found ({ids}, usage FF42:0001)")
            path = info["path"]
            log.info("opening %s (%04X:%04X)", info.get("product_string") or "receiver", vid, info.get("product_id", 0))
        return cls(HidapiTransport(path))

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._t.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- low level ----------------------------------------------------------------
    def _write(self, buf: bytes, label: str) -> None:
        gap = MIN_WRITE_GAP_S - (self._clock() - self._last_write)
        if gap > 0:
            self._sleep(gap)
        n = self._t.write(buf)
        self._last_write = self._clock()
        log.debug(">> %-24s %s (%d B)", label, hexdump(buf, 4), n)

    def _read(self, timeout_ms: int = READ_TIMEOUT_MS) -> bytes:
        return self._t.read(P.MSG_SIZE + 1, timeout_ms)

    def drain(self) -> int:
        """Drop queued reports so the next read answers our next request."""
        n = 0
        while True:
            r = self._read(FLUSH_TIMEOUT_MS)
            if not r:
                return n
            n += 1
            log.debug("<< drained                  %s", hexdump(r, 8))

    def get_property(self, route: int, prop: int, label: str = "") -> Reply | None:
        """Send one get and return the first OK reply within the window, else the first reply seen."""
        label = label or f"get {route:02x}/{prop:02x}"
        self._write(P.frame(route, prop), label)
        first: Reply | None = None
        deadline = self._clock() + REPLY_WINDOW_S
        while self._clock() < deadline:
            raw = self._read()
            if not raw:
                break
            r = P.parse_reply(raw)
            log.debug("<< %-24s %s%s", label, hexdump(raw, 8), " NAK" if r and r.nak else "")
            if r is None:
                continue
            if first is None:
                first = r
            if r.ok:
                return r
        return first

    # ---- protocol level -------------------------------------------------------------
    def handshake(self) -> bool:
        """HeadsetControl's wakeDevice(): fw query, receiver heartbeat, headset heartbeat.
        Does not switch the headset into software mode. True when the headset answered."""
        self.drain()
        self.get_property(P.ROUTE_RECEIVER, P.PROP_FIRMWARE, "receiver firmware")
        self.get_property(P.ROUTE_RECEIVER, P.PROP_HEARTBEAT, "receiver heartbeat")
        self.drain()
        r = self.get_property(P.ROUTE_HEADSET, P.PROP_HEARTBEAT, "headset heartbeat")
        self.drain()
        return r is not None and r.ok

    def read_battery(self, handshake_fallback: bool = True) -> tuple[float | None, Reply | None]:
        """(percent, reply). percent is None when the headset gave no valid level."""
        self.drain()
        last: Reply | None = None
        for attempt in range(BATTERY_ATTEMPTS):
            r = self.get_property(P.ROUTE_HEADSET, P.PROP_BATTERY, "battery")
            pct = P.decode_battery(r)
            if pct is not None:
                return pct, r
            last = r if r is not None else last
            if attempt == 0 and handshake_fallback:
                log.debug("no valid battery reply; running wake handshake")
                self.handshake()
        return None, last

    def read_charge(self) -> bool | None:
        return self.read_charge_raw()[0]

    def read_charge_raw(self) -> tuple[bool | None, int | None]:
        """(charging, raw property 0x10 value). Raw is None when there was no OK reply."""
        r = self.get_property(P.ROUTE_HEADSET, P.PROP_CHARGE, "charge state")
        return P.decode_charge(r), (r.value if r is not None and r.ok else None)

    def read_mic_muted(self) -> bool | None:
        return P.decode_mic_muted(self.get_property(P.ROUTE_HEADSET, P.PROP_MIC, "mic mute"))
