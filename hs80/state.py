from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum


class Status(Enum):
    OK = "ok"                # valid level in the last read
    ASLEEP = "asleep"        # receiver present, headset gives no valid reply (off / asleep / out of range)
    NO_DONGLE = "no_dongle"  # no FF42/0001 collection enumerated
    ERROR = "error"          # open/write/read raised; retrying with backoff


@dataclass(frozen=True)
class BatteryState:
    status: Status = Status.NO_DONGLE
    percent: float | None = None        # current level when status is OK
    charging: bool | None = None        # when status is OK and the charge read answered
    charge_raw: int | None = None       # raw value of property 0x10 (meaning still being established)
    last_percent: float | None = None   # last level ever read (kept through ASLEEP/ERROR)
    last_ok: datetime | None = None     # wall-clock time of the last OK read
    read_ms: int = 0                    # duration of the last poll
    error: str | None = None            # message when status is ERROR
    polls: int = 0

    def summary(self) -> str:
        if self.status is Status.OK:
            charge = "charging" if self.charging else "not charging" if self.charging is not None else "charge ?"
            return f"{self.percent:.1f}% {charge}"
        if self.status is Status.ASLEEP:
            last = f", last {self.last_percent:.0f}%" if self.last_percent is not None else ""
            return f"headset asleep/off{last}"
        if self.status is Status.NO_DONGLE:
            return "receiver not found"
        return f"error: {self.error}"

    def changed_from(self, other: "BatteryState") -> bool:
        """True when something the UI shows differs."""
        return (self.status, self.percent, self.charging) != (other.status, other.percent, other.charging)

    def with_(self, **kw) -> "BatteryState":
        return replace(self, **kw)
