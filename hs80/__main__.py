"""CLI: python -m hs80 [--watch] [--interval S] [--minutes M] [--verbose] [--json]

One-shot prints e.g.   45.0% not charging (212 ms)
--watch prints one line per poll until Ctrl-C or --minutes elapse.
Exit code 0 when the last poll was OK, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time

from .poller import Poller
from .state import BatteryState, Status


def fmt(st: BatteryState) -> str:
    ts = time.strftime("%H:%M:%S")
    raw = f"  chg_raw={st.charge_raw}" if st.charge_raw is not None else ""
    return f"{ts}  #{st.polls:<4d} {st.status.value:<9s} {st.summary():<28s} {st.read_ms:4d} ms{raw}"


def as_json(st: BatteryState) -> str:
    return json.dumps({
        "status": st.status.value, "percent": st.percent, "charging": st.charging, "charge_raw": st.charge_raw,
        "last_percent": st.last_percent, "last_ok": st.last_ok.isoformat() if st.last_ok else None,
        "read_ms": st.read_ms, "polls": st.polls, "error": st.error,
    })


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hs80")
    ap.add_argument("--watch", action="store_true", help="poll continuously")
    ap.add_argument("--interval", type=float, default=60, help="seconds between polls (default 60)")
    ap.add_argument("--minutes", type=float, default=0, help="stop --watch after this long (0 = until Ctrl-C)")
    ap.add_argument("--verbose", action="store_true", help="log every HID frame to stderr")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    ap.add_argument("--vid", default="0x1B1C", help="USB vendor id (default 0x1B1C Corsair)")
    ap.add_argument("--pid", action="append", metavar="HEX",
                    help="receiver product id, repeatable (default 0x0A97 HS80 Max; e.g. --pid 0x2A08 --pid 0x2A02)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)

    vid = int(str(args.vid), 16)
    pids = tuple(int(str(p), 16) for p in (args.pid or ["0x0A97"]))

    out = as_json if args.json else (fmt if args.watch else lambda st: f"{st.summary()} ({st.read_ms} ms)")

    if not args.watch:
        p = Poller(args.interval, vid=vid, pids=pids)
        st = p.poll_once()
        p.stop()
        print(out(st))
        return 0 if st.status is Status.OK else 1

    done = threading.Event()
    counts = {"polls": 0, "ok": 0, "worst_ms": 0}

    def on_poll(st: BatteryState) -> None:
        counts["polls"] += 1
        counts["ok"] += st.status is Status.OK
        counts["worst_ms"] = max(counts["worst_ms"], st.read_ms)
        print(out(st), flush=True)

    p = Poller(args.interval, on_poll=on_poll, vid=vid, pids=pids)
    p.start()
    try:
        if args.minutes > 0:
            done.wait(args.minutes * 60)
        else:
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        p.stop()
    print(f"done: {counts['ok']}/{counts['polls']} polls OK, worst {counts['worst_ms']} ms", flush=True)
    return 0 if p.state.status is Status.OK else 1


if __name__ == "__main__":
    sys.exit(main())
