"""Offline test doubles: a scripted HID transport replaying the bytes captured in RESULTS.md."""
from __future__ import annotations

from collections import deque
from typing import Callable

from hs80 import protocol as P

# Replies captured on hardware 2026-10-16 (first bytes; rest zero to 64)
CAPTURED = {
    "fw":        bytes.fromhex("01 00 02 00 00 0a 42 00".replace(" ", "")),
    "rx_hb":     bytes.fromhex("01 00 02 00 97 0a 00 00".replace(" ", "")),
    "hs_hb":     bytes.fromhex("01 01 02 00 96 0a 00 00".replace(" ", "")),
    "battery45": bytes.fromhex("01 01 02 00 c2 01 00 00".replace(" ", "")),   # 450 -> 45.0 %
    "charge2":   bytes.fromhex("01 01 02 00 02 00 00 00".replace(" ", "")),   # discharging
    "mic1":      bytes.fromhex("01 01 02 00 01 00 00 00".replace(" ", "")),   # muted
}


def pad(b: bytes) -> bytes:
    return b + bytes(P.MSG_SIZE - len(b))


def reply(route: int, value: int, err: int = 0, cmd: int = P.CMD_GET) -> bytes:
    return pad(bytes([0x01, route, cmd, err, value & 0xFF, (value >> 8) & 0xFF]))


NAK = pad(bytes([0x01, 0xF0, 0x02]))
HEADSET_ID = reply(P.REPLY_ROUTE_HEADSET, 2710)   # "value is the paired headset id" shape
ZERO = reply(P.REPLY_ROUTE_HEADSET, 0)
BATTERY_45 = pad(CAPTURED["battery45"])
CHARGE_2 = pad(CAPTURED["charge2"])
CHARGE_1 = reply(P.REPLY_ROUTE_HEADSET, 1)


def default_responder(frame: bytes) -> list[bytes]:
    """Answers like the awake headset did during the Phase 0 poll."""
    route, cmd, prop = frame[1], frame[2], frame[3]
    if route == P.ROUTE_RECEIVER and prop == P.PROP_FIRMWARE:
        return [pad(CAPTURED["fw"])]
    if route == P.ROUTE_RECEIVER and prop == P.PROP_HEARTBEAT:
        return [pad(CAPTURED["rx_hb"])]
    if route == P.ROUTE_HEADSET and prop == P.PROP_HEARTBEAT:
        return [pad(CAPTURED["hs_hb"])]
    if route == P.ROUTE_HEADSET and prop == P.PROP_BATTERY:
        return [BATTERY_45]
    if route == P.ROUTE_HEADSET and prop == P.PROP_CHARGE:
        return [CHARGE_2]
    if route == P.ROUTE_HEADSET and prop == P.PROP_MIC:
        return [pad(CAPTURED["mic1"])]
    return []


class FakeTransport:
    def __init__(self, responder: Callable[[bytes], list[bytes]] = default_responder,
                 preloaded: list[bytes] | None = None):
        self.responder = responder
        self.writes: list[bytes] = []
        self.queue: deque[bytes] = deque(preloaded or [])
        self.closed = False
        self.fail_write: Exception | None = None

    def write(self, buf: bytes) -> int:
        if self.fail_write:
            raise self.fail_write
        self.writes.append(bytes(buf))
        self.queue.extend(self.responder(bytes(buf)))
        return len(buf)

    def read(self, size: int, timeout_ms: int) -> bytes:
        return self.queue.popleft() if self.queue else b""

    def close(self) -> None:
        self.closed = True

    # helpers for assertions
    def props_sent(self) -> list[tuple[int, int]]:
        return [(w[1], w[3]) for w in self.writes]


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s
