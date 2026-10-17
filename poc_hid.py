"""Phase 0 proof of concept: read the Corsair HS80 Max battery over the Slipstream
receiver's vendor HID collection, without iCUE.

Protocol (as actually sent by Sapd/HeadsetControl 4.1.0 corsair_void_v2w.hpp,
BrokeSkill/hs80-max-control Hs80.Core, and HeyOkay/HaloBattery providers/corsair.py):

  * Collection: VID 0x1B1C, PID 0x0A97, usage page 0xFF42, usage 0x0001.
    Selected by usage page/usage, never by interface index.
  * Request = one 64-byte output report. Byte 0 is the Windows report ID 0x02,
    then <route> <cmd> <property>:
        route 0x08 = receiver, 0x09 = headset behind it
        cmd   0x02 = get property, 0x01 = set property
        props 0x13 firmware, 0x12 heartbeat, 0x0F battery, 0x10 charge state, 0xA6 mic mute
    (HeadsetControl writes 65 bytes "00 02 09 02 0F" through hidapi on Linux; the
    leading 0x00 is hidapi's "no report ID" marker, so the frame on the wire is the
    same 64 bytes starting 02.)
  * Reply = 64-byte input report: [0]=report id, [1]=route, [2]=cmd, [3]=err (0 = OK),
    [4],[5] = little-endian uint16. For property 0x0F that is the battery level in
    TENTHS of a percent (0..1000). All three projects divide by 10; PLAN.md's
    "hundredths" is wrong. 0 or >1000 means the headset did not answer (the
    receiver sometimes answers with the paired headset id) -> unknown, retry.
    A reply starting 01 F0 is a NAK (hs80-max-control).
  * Wake handshake before the first battery read (HeadsetControl wakeDevice):
    receiver firmware query, receiver heartbeat, headset heartbeat. It does not
    switch the headset into software mode, so there is no audible pop and iCUE's
    settings are not touched. hs80-max-control skips the handshake entirely and
    just rate-limits (>= 50 ms between writes, >= 2 s between battery reads).

Usage:
    python poc_hid.py                           one read, verbose
    python poc_hid.py --poll 30 --minutes 10    poll every 30 s for 10 min
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

import hid  # pip install hidapi  (cython-hidapi; its Windows wheel has hidapi compiled in.
            #  The "hid" package (pyhidapi) needs a separate hidapi.dll and was not used.)

VID, PID = 0x1B1C, 0x0A97
USAGE_PAGE, USAGE = 0xFF42, 0x0001

REPORT_ID = 0x02
ROUTE_RECEIVER, ROUTE_HEADSET = 0x08, 0x09
CMD_GET = 0x02
PROP_FIRMWARE, PROP_HEARTBEAT = 0x13, 0x12
PROP_BATTERY, PROP_CHARGE, PROP_MIC = 0x0F, 0x10, 0xA6

MSG_SIZE = 64
READ_TIMEOUT_MS = 500       # HeadsetControl hsc_device_timeout / HaloBattery READ_TIMEOUT_MS
FLUSH_TIMEOUT_MS = 30
MIN_WRITE_GAP_S = 0.05      # hs80-max-control MinWriteGapMs
BATTERY_ATTEMPTS = 3        # HeadsetControl battery_level_max_attempts
LEVEL_MAX = 1000            # tenths of a percent

CHARGE_NAMES = {0: "discharging", 1: "charging", 2: "charge complete"}


def hexdump(b) -> str:
    return " ".join(f"{x:02x}" for x in b)


def frame(route: int, prop: int, cmd: int = CMD_GET) -> bytes:
    head = bytes([REPORT_ID, route, cmd, prop])
    return head + bytes(MSG_SIZE - len(head))


def find_control_path() -> bytes:
    """Path of the FF42/0001 collection of the HS80 Max receiver."""
    found = []
    for d in hid.enumerate(VID, PID):
        found.append(d)
        if d.get("usage_page") == USAGE_PAGE and d.get("usage") == USAGE:
            return d["path"]
    for d in found:
        print(f"  seen: iface={d.get('interface_number')} usage={d.get('usage_page', 0):04x}:"
              f"{d.get('usage', 0):04x} path={d['path']!r}", file=sys.stderr)
    raise SystemExit(f"no {USAGE_PAGE:04X}:{USAGE:04X} collection on {VID:04X}:{PID:04X} "
                     f"({len(found)} collections seen). Is the receiver plugged in?")


class Hs80:
    def __init__(self, path: bytes, verbose: bool = True):
        self.dev = hid.device()
        self.dev.open_path(path)
        self.dev.set_nonblocking(0)   # timed reads below, never an untimed blocking read
        self.verbose = verbose
        self._last_write = 0.0
        self.log: list[str] = []

    def close(self):
        self.dev.close()

    def _say(self, msg: str):
        self.log.append(msg)
        if self.verbose:
            print(msg)

    def write(self, buf: bytes, label: str) -> None:
        gap = MIN_WRITE_GAP_S - (time.monotonic() - self._last_write)
        if gap > 0:
            time.sleep(gap)
        try:
            n = self.dev.write(buf)
        except Exception as e:
            # Some receivers refuse one framing; try the 65-byte hidapi form once.
            self._say(f"  write {label} ({len(buf)} B) failed: {e}; retrying with leading 0x00")
            n = self.dev.write(b"\x00" + buf)
        self._last_write = time.monotonic()
        self._say(f"  >> {label}: {hexdump(buf[:8])} ... ({n} B written)")

    def read(self, timeout_ms: int = READ_TIMEOUT_MS):
        """One timed read; returns bytes or None on timeout. Never blocks forever."""
        r = self.dev.read(MSG_SIZE + 1, timeout_ms)
        return bytes(r) if r else None

    def drain(self, label: str = "drain") -> int:
        n = 0
        while True:
            r = self.read(FLUSH_TIMEOUT_MS)
            if not r:
                return n
            n += 1
            self._say(f"  << {label}: {hexdump(r[:12])}")

    def wake(self) -> bool:
        """HeadsetControl wakeDevice(): fw query, receiver heartbeat, headset heartbeat."""
        self.drain("stale")
        self.write(frame(ROUTE_RECEIVER, PROP_FIRMWARE), "receiver firmware query")
        r = self.read()
        self._say(f"  << fw reply: {hexdump(r[:12]) if r else 'timeout'}")
        self.write(frame(ROUTE_RECEIVER, PROP_HEARTBEAT), "receiver heartbeat")
        r = self.read()
        self._say(f"  << rx heartbeat reply: {hexdump(r[:12]) if r else 'timeout'}")
        self.drain()
        self.write(frame(ROUTE_HEADSET, PROP_HEARTBEAT), "headset heartbeat")
        r = self.read()
        self._say(f"  << headset heartbeat reply: {hexdump(r[:12]) if r else 'timeout'}")
        self.drain()
        return r is not None

    def get_property(self, route: int, prop: int, label: str):
        """Send a get and return the first reply that is an OK answer to a get, else None."""
        self.write(frame(route, prop), label)
        first = None
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            r = self.read()
            if not r:
                break
            self._say(f"  << {label} reply: {hexdump(r[:12])}")
            if first is None:
                first = r
            if r[0] == 0x01 and r[1] == 0xF0:
                self._say("     (NAK)")
                continue
            if len(r) >= 6 and r[2] == CMD_GET and r[3] == 0x00:
                return r
        return first

    def battery(self):
        """Returns (percent float or None, raw reply bytes or None)."""
        for attempt in range(1, BATTERY_ATTEMPTS + 1):
            r = self.get_property(ROUTE_HEADSET, PROP_BATTERY, f"battery req (try {attempt})")
            if not r or len(r) < 6 or r[3] != 0x00:
                continue
            raw = r[4] | (r[5] << 8)
            if raw == 0 or raw > LEVEL_MAX:
                self._say(f"     value {raw} out of range 1..{LEVEL_MAX} -> unknown, retrying")
                continue
            return raw / 10.0, r
        return None, None

    def charge_state(self):
        r = self.get_property(ROUTE_HEADSET, PROP_CHARGE, "charge-state req")
        if r and len(r) >= 5 and r[3] == 0x00:
            return r[4], r
        return None, r

    def mic_muted(self):
        r = self.get_property(ROUTE_HEADSET, PROP_MIC, "mic-mute req")
        if r and len(r) >= 5 and r[3] == 0x00:
            return bool(r[4]), r
        return None, r


def one_read(path: bytes, verbose: bool, extras: bool, wake: bool = True) -> tuple:
    t0 = time.perf_counter()
    h = Hs80(path, verbose=verbose)
    try:
        if wake:
            awake = h.wake()
        else:
            h.drain("stale")
            awake = None   # handshake skipped (hs80-max-control style)
        t_wake = time.perf_counter() - t0
        pct, raw = h.battery()
        t_batt = time.perf_counter() - t0
        charge = mic = None
        if extras and pct is not None:
            charge, _ = h.charge_state()
            mic, _ = h.mic_muted()
        return awake, pct, raw, charge, mic, t_wake, t_batt
    finally:
        h.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--poll", type=float, metavar="SECONDS", help="poll interval")
    ap.add_argument("--minutes", type=float, default=10, help="poll duration (default 10)")
    ap.add_argument("--quiet", action="store_true", help="only print the summary line")
    ap.add_argument("--no-extras", action="store_true", help="skip charge-state and mic reads")
    ap.add_argument("--no-wake", action="store_true", help="skip the 3-write wake handshake (hs80-max-control style)")
    args = ap.parse_args()

    path = find_control_path()
    print(f"control collection: {path!r}")

    if not args.poll:
        awake, pct, raw, charge, mic, t_wake, t_batt = one_read(path, not args.quiet, not args.no_extras, not args.no_wake)
        print()
        print(f"handshake answered: {awake}  (handshake {t_wake*1000:.0f} ms, battery {t_batt*1000:.0f} ms total)")
        if pct is None:
            print("battery: UNKNOWN (no valid reply; headset off/asleep?)")
            return 1
        print(f"battery raw reply: {hexdump(raw)}")
        print(f"battery: {pct:.1f}%  (raw uint16 {raw[4] | (raw[5] << 8)} tenths of a percent)")
        if charge is not None:
            print(f"charge state: {charge} = {CHARGE_NAMES.get(charge, '?')}")
        if mic is not None:
            print(f"mic muted: {mic}")
        return 0

    end = time.monotonic() + args.minutes * 60
    n = ok = hung = 0
    worst = 0.0
    print(f"polling every {args.poll:g} s for {args.minutes:g} min; Ctrl-C to stop")
    while True:
        n += 1
        ts = datetime.now().strftime("%H:%M:%S")
        try:
            awake, pct, raw, charge, mic, t_wake, t_batt = one_read(path, False, not args.no_extras, not args.no_wake)
        except Exception as e:
            print(f"{ts} #{n:3d} ERROR {type(e).__name__}: {e}", flush=True)
            if time.monotonic() >= end:
                break
            time.sleep(args.poll)
            continue
        worst = max(worst, t_batt)
        if t_batt > 5:
            hung += 1
        if pct is None:
            print(f"{ts} #{n:3d} UNKNOWN  awake={awake} ({t_batt*1000:.0f} ms)", flush=True)
        else:
            ok += 1
            extra = ""
            if charge is not None:
                extra += f" charge={charge}({CHARGE_NAMES.get(charge, '?')})"
            if mic is not None:
                extra += f" mic_muted={mic}"
            print(f"{ts} #{n:3d} {pct:5.1f}%  raw=[{hexdump(raw[:8])}] {t_batt*1000:4.0f} ms{extra}", flush=True)
        if time.monotonic() >= end:
            break
        time.sleep(args.poll)
    print(f"done: {ok}/{n} reads valid, worst round-trip {worst*1000:.0f} ms, {hung} reads over 5 s")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
