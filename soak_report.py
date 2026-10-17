"""Summarise the widget's soak from its log (and iCUE's log, if present).

    python soak_report.py [--since "2026-10-17 14:51"] [--log PATH]

Default log: the real %APPDATA%\\HS80Widget\\widget.log via \\\\localhost\\C$ so it also works
when run from a sandboxed host that redirects %APPDATA%.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
from collections import Counter
from datetime import datetime

USER = os.environ.get("USERNAME") or os.path.basename(os.path.expanduser("~"))
REAL_LOG = rf"\\localhost\C$\Users\{USER}\AppData\Roaming\HS80Widget\widget.log"
ICUE_LOGS = rf"\\localhost\C$\Users\{USER}\AppData\Local\Corsair\Logs\CUE5\*.log"
TS = "%Y-%m-%d %H:%M:%S"


def parse(path: str, since: datetime):
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ (\w+) ([\w.]+): (.*)", line.rstrip())
            if not m:
                continue
            t = datetime.strptime(m.group(1), TS)
            if t >= since:
                rows.append((t, m.group(2), m.group(3), m.group(4)))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-10-17 15:20")
    ap.add_argument("--log", default=REAL_LOG)
    a = ap.parse_args()
    since = datetime.strptime(a.since, "%Y-%m-%d %H:%M")
    rows = parse(a.log, since)
    if not rows:
        print("no log lines since", since)
        return 1
    print(f"widget log: {a.log}\nwindow: {since} .. {rows[-1][0]}  ({(rows[-1][0] - since).total_seconds() / 3600:.1f} h)\n")

    starts = [r for r in rows if r[2] == "hs80.main" and "starting" in r[3]]
    beats = [r for r in rows if "heartbeat:" in r[3]]
    states = [r for r in rows if "state ->" in r[3]]
    warns = [r for r in rows if r[1] in ("WARNING", "ERROR")]
    toasts = [r for r in rows if r[2] == "hs80.notify"]

    print(f"starts: {len(starts)}   state changes: {len(states)}   toasts: {len(toasts)}   warnings/errors: {len(warns)}")
    print("\nheartbeats (hourly):")
    for t, _, _, msg in beats:
        print(" ", t.strftime("%m-%d %H:%M"), msg.replace("heartbeat: ", ""))
    if beats:
        rss = [float(re.search(r"rss=(-?[\d.]+)", b[3]).group(1)) for b in beats]
        print(f"  rss first {rss[0]:.0f} MB, last {rss[-1]:.0f} MB, max {max(rss):.0f} MB")
        polls = [int(re.search(r"polls=(\d+)", b[3]).group(1)) for b in beats]
        if len(polls) > 1:
            per_h = (polls[-1] - polls[0]) / max(1, len(polls) - 1)
            print(f"  polls per hour between heartbeats: {per_h:.0f} (expected ~60 at a 60 s interval)")

    kinds = Counter(re.sub(r"[\d.]+%", "N%", s[3].split("(")[0]).strip() for s in states)
    print("\nstate kinds:")
    for k, n in kinds.most_common():
        print(f"  {n:4d}  {k}")
    print("\nlast 8 state changes:")
    for t, _, _, msg in states[-8:]:
        print(" ", t.strftime("%m-%d %H:%M:%S"), msg)
    if warns:
        print("\nwarnings/errors:")
        for t, lvl, name, msg in warns[-20:]:
            print(" ", t.strftime("%m-%d %H:%M:%S"), lvl, name, msg[:120])

    logs = sorted(glob.glob(ICUE_LOGS), key=os.path.getmtime)
    if logs:
        n_warn = n_other = 0
        per_hour: Counter = Counter()
        with open(logs[-1], encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d):", line)
                if not m or datetime.strptime(m.group(1), "%Y-%m-%d %H:%M") < since:
                    continue
                if "No command candidates for unexpected reply" in line:
                    n_warn += 1
                    per_hour[m.group(1)[:13]] += 1
                else:
                    n_other += 1
        print(f"\niCUE log ({os.path.basename(logs[-1])}): {n_warn} 'unexpected reply' warnings, {n_other} other lines since {since}")
        if per_hour:
            vals = sorted(per_hour.values())
            print(f"  warnings per hour: min {vals[0]}, median {vals[len(vals)//2]}, max {vals[-1]} (expected ~120 at 2 per 60 s poll)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
