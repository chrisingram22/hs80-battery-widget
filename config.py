"""config.json in %APPDATA%\\HS80Widget (interval, thresholds, alerts, window position)."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, replace

log = logging.getLogger("hs80.config")

APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "HS80Widget")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
LOG_PATH = os.path.join(APP_DIR, "widget.log")

INTERVAL_CHOICES = (30, 60, 120, 300)
DEFAULT_VID = 0x1B1C
# Receivers HeadsetControl drives with the same protocol. Only 0x0A97 is verified with this widget;
# the first id that is plugged in wins, absent ids cost nothing, and the widget only ever sends reads.
DEFAULT_PIDS = (0x0A97,         # HS80 Max Wireless (verified)
                0x2A08,         # Void Wireless v2 (untested)
                0x2A02)         # Virtuoso Max (untested)


def parse_id(v) -> int | None:
    """USB id from an int or a string like '0x2A08', '2A08', '10760'. None when unusable."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if 0 < v <= 0xFFFF else None
    if isinstance(v, str):
        t = v.strip().lower()
        try:
            n = int(t, 16) if (t.startswith("0x") or any(c in t for c in "abcdef")) else int(t, 10)
        except ValueError:
            try:
                n = int(t, 16)
            except ValueError:
                return None
        return n if 0 < n <= 0xFFFF else None
    return None


@dataclass(frozen=True)
class Config:
    interval_s: int = 60
    thresholds: tuple[int, ...] = (30, 10)      # low-battery toasts, high to low
    alerts: bool = True
    disconnect_alert: bool = False
    show_float: bool = False                     # floating window visible
    float_pos: tuple[int, int] | None = None     # its top-left corner, screen px
    click_through: bool = False                  # floating window ignores the mouse (toggle from the tray)
    last_alerts: dict = field(default_factory=dict)   # {"30": iso-time of the last toast at that threshold}
    vendor_id: int = DEFAULT_VID                 # USB vendor id of the receiver
    product_ids: tuple[int, ...] = DEFAULT_PIDS  # receiver product ids to look for, first match wins

    def with_(self, **kw) -> "Config":
        return replace(self, **kw)


def _coerce(d: dict) -> Config:
    base = Config()
    out = {}
    for k, default in asdict(base).items():
        v = d.get(k, default)
        if k == "thresholds":
            try:
                v = tuple(sorted({int(x) for x in v}, reverse=True)) or base.thresholds
            except (TypeError, ValueError):
                v = base.thresholds
        elif k == "float_pos":
            v = tuple(int(x) for x in v) if isinstance(v, (list, tuple)) and len(v) == 2 else None
        elif k == "vendor_id":
            v = parse_id(v) or base.vendor_id
        elif k == "product_ids":
            items = v if isinstance(v, (list, tuple)) else [v]
            ids = []
            for x in items:
                n = parse_id(x)
                if n is not None and n not in ids:
                    ids.append(n)
            v = tuple(ids) or base.product_ids
        elif k == "last_alerts":
            v = {str(int(t)): str(ts) for t, ts in v.items()} if isinstance(v, dict) else {}
        elif k == "interval_s":
            v = int(v) if isinstance(v, (int, float)) and v >= 5 else base.interval_s
        elif isinstance(default, bool):
            v = bool(v)
        out[k] = v
    return Config(**out)


def load(path: str = CONFIG_PATH) -> Config:
    try:
        with open(path, encoding="utf-8-sig") as f:      # -sig: tolerate a BOM from PowerShell/Notepad
            return _coerce(json.load(f))
    except FileNotFoundError:
        return Config()
    except (OSError, ValueError) as e:
        log.warning("config unreadable (%s); using defaults", e)
        return Config()


def save(cfg: Config, path: str = CONFIG_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    d = asdict(cfg)
    d["vendor_id"] = f"0x{cfg.vendor_id:04X}"                       # hex strings: readable, round-trip via parse_id
    d["product_ids"] = [f"0x{p:04X}" for p in cfg.product_ids]
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, path)
