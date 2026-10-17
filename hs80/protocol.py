"""Wire protocol of the HS80 Max Slipstream receiver (iCUE calls it "bragi").

Pure functions only; no I/O. Byte layouts verified on hardware, see RESULTS.md.

Request: one 64-byte output report
    [0] report ID 0x02   [1] route   [2] cmd   [3] property   [4..] zero
Reply: one 64-byte input report
    [0] report ID 0x01   [1] route   [2] cmd echo   [3] err (0 = OK)   [4..5] uint16 LE value
A reply starting 01 F0 is a NAK.
"""
from __future__ import annotations

from dataclasses import dataclass

VID, PID = 0x1B1C, 0x0A97
USAGE_PAGE, USAGE = 0xFF42, 0x0001

REPORT_ID = 0x02
ROUTE_RECEIVER, ROUTE_HEADSET = 0x08, 0x09
CMD_GET = 0x02
PROP_FIRMWARE, PROP_HEARTBEAT = 0x13, 0x12
PROP_BATTERY, PROP_CHARGE, PROP_MIC = 0x0F, 0x10, 0xA6

MSG_SIZE = 64
LEVEL_MAX = 1000            # battery value is tenths of a percent, 1..1000 valid
CHARGE_CHARGING = 1         # property 0x10: 1 = charging; 2 observed while discharging

# Reply route byte: 0 = receiver, 1 = headset behind it
REPLY_ROUTE_RECEIVER, REPLY_ROUTE_HEADSET = 0x00, 0x01


def hexdump(b, limit: int | None = None) -> str:
    data = bytes(b)
    if limit is not None:
        data = data[:limit]
    return " ".join(f"{x:02x}" for x in data)


def frame(route: int, prop: int, cmd: int = CMD_GET) -> bytes:
    """Build a 64-byte get/set request."""
    head = bytes([REPORT_ID, route, cmd, prop])
    return head + bytes(MSG_SIZE - len(head))


@dataclass(frozen=True)
class Reply:
    route: int
    cmd: int
    err: int
    value: int          # uint16 LE at [4..5]
    raw: bytes
    nak: bool = False

    @property
    def ok(self) -> bool:
        """True for a successful answer to a get."""
        return not self.nak and self.cmd == CMD_GET and self.err == 0


def parse_reply(data) -> Reply | None:
    """Parse one input report. Returns None when it is too short to be a reply."""
    b = bytes(data)
    if len(b) >= 2 and b[0] == 0x01 and b[1] == 0xF0:
        return Reply(route=0xF0, cmd=b[2] if len(b) > 2 else 0, err=0xFF, value=0, raw=b, nak=True)
    if len(b) < 6:
        return None
    return Reply(route=b[1], cmd=b[2], err=b[3], value=b[4] | (b[5] << 8), raw=b)


def decode_battery(reply: Reply | None) -> float | None:
    """Percent (0.1 resolution) from a battery reply, or None when there is no usable value.

    0 and values above 1000 mean the headset did not answer (the receiver sometimes
    answers with the paired headset ID instead), never a level.
    """
    if reply is None or not reply.ok:
        return None
    if reply.value == 0 or reply.value > LEVEL_MAX:
        return None
    return reply.value / 10.0


def decode_charge(reply: Reply | None) -> bool | None:
    """True when charging, False when not, None when there is no usable answer."""
    if reply is None or not reply.ok:
        return None
    return reply.value == CHARGE_CHARGING


def decode_mic_muted(reply: Reply | None) -> bool | None:
    if reply is None or not reply.ok:
        return None
    return reply.value != 0
