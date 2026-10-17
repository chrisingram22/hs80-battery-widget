# HS80 Max Battery Widget — Development Plan

_Written 2026-10-16 after Phase 0. Supersedes the "Phased plan" section of PLAN.md.
Facts about the protocol and the test results are in RESULTS.md._

## Goal

A small Windows app that always shows the HS80 Max battery percentage: a system-tray icon
with the number drawn in it (v1), an optional floating always-on-top mini window (v2), and a
toast when the battery gets low. It reads the headset directly over the Slipstream receiver
(Path A), works whether or not iCUE is running, starts with Windows, and never hangs.

## Decisions already made

| Decision | Choice | Why |
|---|---|---|
| Data path | Direct HID (Path A) only | Proven in Phase 0: 21/21 reads, ~230 ms, no iCUE disturbance. SDK path needs a download and gives no better data. |
| Language / stack | Python 3.12, `hidapi`, `pystray`, `Pillow`, `tkinter`, `win11toast`, `PyInstaller` | Already installed and working in `.venv`. No .NET 8 on the PC. |
| HID binding | `hidapi` wheel (cython-hidapi) | The `hid` package cannot find hidapi.dll on Windows. |
| Collection selection | usage page 0xFF42 / usage 0x0001 | Interface and collection numbers differ between PCs (MI_03&Col04 here). |
| Battery decode | uint16 at reply[4..5], tenths of a percent, valid 1..1000 | Confirmed on hardware (450 = 45.0 %). |
| Request strategy | Bare battery request first; three-write handshake only when that gets no valid reply | 58–68 ms and 1 iCUE log line instead of 6. |
| Charging | Property 0x10: **1 = charging, 2 = on battery** (confirmed 15:25 capture, flipped within 1 s of plug-in). Anything else → unknown | One unexplained miss during the 14:32 plug-in; raw value kept in `BatteryState.charge_raw`, re-check in the M5 soak. |
| Per-poll requests | battery + charge only; no mic read, no firmware query | Minimises iCUE log noise. |
| Never send | software-mode set `02 08 01 03 00 02` or any `cmd 0x01` set | Would take control from iCUE and cause the audible pop. |

## Architecture

One process, two threads. No admin rights needed.

```
main.py                 entry point (run with pythonw / packaged exe), single-instance guard
hs80/
  protocol.py           constants, frame builder, reply parser, decode (pure functions, unit-tested)
  device.py             Hs80Device: find/open/close, timed read, write gap, get_property,
                        read_battery(), read_charge(), handshake fallback, re-enumerate on failure
  poller.py             background thread: interval, rate gate, retry/backoff, state machine,
                        on_change callback. Owns the device; UI never touches HID directly.
  state.py              BatteryState dataclass: percent, charging, status, last_ok, source
widget/
  icon.py               Pillow: 32x32 / 16x16 icon with percent text and green/amber/red band
  app.py                controller: owns config/poller/alerts/tray/float, shared menu actions, Tk-thread state queue
  tray.py               tooltip text + pystray menu builder (Refresh, Interval, Low-battery alerts,
                        Start with Windows, Show floating widget, Click-through, Open log, Quit)
  float.py              tkinter borderless always-on-top window (v2)
  notify.py             win11toast with per-threshold debounce
config.py               config.json in %APPDATA%\HS80Widget (interval, thresholds, position, autostart)
autostart.py            Startup-folder shortcut create/remove
tests/
  test_protocol.py      decode/validate against the bytes captured in RESULTS.md
  test_poller.py        FakeDevice replaying captured replies, timeouts, out-of-range values
```

**Poller state machine**

| State | Meaning | Icon |
|---|---|---|
| `OK` | valid level in the last read | number + colour band |
| `ASLEEP` | receiver present, headset gives no valid reply (off, asleep, out of range) | grey "zz" with last known % in tooltip |
| `NO_DONGLE` | no FF42/0001 collection enumerated | grey "–" |
| `ERROR` | open/write/read raised; retrying with backoff | grey "!" |

Transitions trigger `on_change`; the UI only redraws on change. Rules carried over from the
reference projects: at least 50 ms between writes, at least 2 s between battery reads,
reply accepted only if `[2] == 0x02 and [3] == 0x00` and value in 1..1000, three attempts
then `ASLEEP`, every read is a 500 ms timed read so nothing can block forever. On three
consecutive `ERROR` polls the device is closed and re-enumerated (receiver stall, unplug).

**Threading.** pystray's loop must own the main thread on Windows, and tkinter must also run
on the thread that created it. v1 has no tkinter, so pystray runs on main and the poller on
a daemon thread. For v2 the floating window is created and driven on the main thread with
pystray started via `icon.run_detached()`; the poller posts updates through a
`queue.Queue` drained by `root.after(200, …)`. This is decided now so v1 is not built in a
way v2 has to undo.

## Milestones

### M1 — Core library and CLI (est. 2–3 h)

Turn `poc_hid.py` into `hs80/` with tests.

- [x] `protocol.py`: frame(), parse_reply(), decode_battery(), decode_charge() + tests using the captured bytes
- [x] `device.py`: open by usage page, timed reads, bare-request-then-handshake, re-enumerate (transport injectable for tests)
- [x] `poller.py` + `state.py` with FakeTransport tests: normal, asleep (value 0 / 2710), timeout, dongle missing, error backoff + reopen, recovery (30 tests, `python -m unittest discover -s tests -t .`)
- [x] `python -m hs80` prints one line (`42.0% not charging (109 ms)` on 2026-10-16 14:31), `--json`, `--verbose`, `--watch --interval --minutes`; replaces `poc_hid.py` for debugging

Done when: tests pass offline, and `python -m hs80 --watch` runs 30 min beside iCUE with no `ERROR`.

**M1 done 2026-10-16 15:01.** 30 tests pass; `--watch --interval 60 --minutes 30` gave 30/30 OK polls, 108–141 ms each, no ERROR/ASLEEP, 2 requests per poll (64 iCUE warning lines for 32 reads, as expected). Log: `watch_log.txt`. During the watch the headset was on USB-C from 14:32:14 to 14:35:11 (iCUE log: `ExternalPowerSourcePresent` change, PID 0A96 inserted/removed); the level read 53 → 64 → 62 % while plugged in and settled back to 45 % after, but **property 0x10 never read 1** in those three polls. The 15:25 capture then showed 0x10 = 1 within 1 s of plug-in, so charging detection works; the 14:32 miss is unexplained (see M5).

### M2 — Tray widget v1 (est. 3–4 h)

- [x] `icon.py`: draw percent (two digits, "100" shrinks) over a colour band; ≥50 green, 30–49 amber, <30 red (matches the first toast); grey variants for the non-OK states; charging shows a small bolt
- [x] `tray.py`: pystray icon, tooltip "HS80 Max: 45 % · not charging · updated 13:33", menu: Refresh now, Interval (30 s / 1 min / 2 min / 5 min), Low-battery alerts (on/off), Start with Windows (toggle), Open log, Quit
- [x] `notify.py`: toast at ≤30 % and ≤10 %, once per crossing, reset when charging or above threshold+5; toast on "headset disconnected" optional (off by default)
- [x] `config.py`: load/save JSON, defaults interval 60 s, thresholds 30/10
- [x] `autostart.py`: shortcut in `shell:startup` pointing at pythonw + main.py (later the exe)
- [x] `main.py`: single-instance mutex, logging to `%APPDATA%\HS80Widget\widget.log` (rotating, 1 MB)

Done when: the icon sits in the tray showing the right number, updates after the headset is
charged/discharged, survives unplugging and replugging the receiver and turning the headset
off and on, and a low-battery toast fires at 30 %.

**M2 status 2026-10-16 15:33.** Code complete, 43 tests pass. Verified on this PC: widget starts with pythonw and logs `state -> 49.0% not charging`; second instance exits (mutex); a test toast displays; Startup shortcut enable/disable round-trips (target = `.venv\Scripts\pythonw.exe main.py`). Chris's first hand test found the widget stuck in ERROR for minutes after a receiver unplug/replug (the poller backed off 60/120/240 s and only re-enumerated after 3 failures). Fixed 15:45: any HID failure drops the handle at once; if the receiver is no longer enumerable the state is NO_DONGLE (grey "–") and it is looked for every 15 s; a replug recovers on the next look. Errors with the receiver present retry at 15/30/60 s. 45 tests. **Verified by Chris 15:44:** receiver unplug → "receiver not found" on the next poll, replug → 49 % 15 s later (widget.log). **M2 done.** Not yet seen in real use: headset off/on (ASLEEP path, tested offline only) and the 30 % toast (logic tested offline, a sample toast displayed); both will show up naturally during M5.

### M3 — Floating widget v2 (est. 2–3 h)

- [x] `float.py`: borderless, always-on-top, semi-transparent tkinter window: battery glyph with fill level + "45 %"; drag to move; position saved; right-click → same menu as tray; double-click hides it
- [x] Tray menu "Show floating widget" toggle, remembered in config
- [x] Click-through option (WS_EX_TRANSPARENT via ctypes) in config, off by default, toggled from the tray menu

Done when: it can be shown/hidden from the tray, stays where it was put after restart, and
its number matches the tray icon.

**M3 status 2026-10-16 15:50.** Code complete, 51 tests pass (floating-window smoke tests create a real withdrawn Tk root). Restructured as planned: `widget/app.py` is the controller (config, poller, alerts, tray, float, shared actions); `widget/tray.py` is now only the tooltip and pystray menu builder; `main.py` creates the Tk root on the main thread, pystray runs detached, the poller posts states through a queue drained every 200 ms. Verified: window shows at top-right with glyph + 49 % (screen capture). Found and fixed: `config.load` rejected a UTF-8 BOM (now `utf-8-sig`). **Verified by Chris 2026-10-16 15:52:** drag (position saved as 3269,55), double-click hide, right-click menu, tray toggle and click-through all work (widget.log). At 16:09 the headset went off and the widget showed "headset asleep/off, last 48%" — first real-use confirmation of the ASLEEP path. **M3 done.** Position persistence across a restart is implied by the config but not yet watched explicitly.

### M4 — Packaging (est. 1–2 h)

- [x] `build.ps1`: PyInstaller `--onefile --noconsole --icon assetspp.ico`, `--hidden-import win11toast`, `--collect-submodules/-binaries/-data winrt`, `--hidden-import hid`; 21.2 MB; `-Install` copies to `%LOCALAPPDATA%\Programs\HS80BatteryWidget` and starts it; stops a running instance first
- [x] Autostart shortcut now points at the exe (`autostart.refresh_if_stale()` rewrites a venv-era shortcut on exe start, never the other way round); config/log stay in `%APPDATA%`
- [x] `README.md`: install, uninstall, icon legend, what it sends, known limitations, build

Done when: a clean copy of the exe on this PC starts from the Startup folder after a reboot
and shows the percentage within 10 s of login.

**M4 status 2026-10-17 14:33.** Built and installed; the exe runs from `%LOCALAPPDATA%\Programs\HS80BatteryWidget`, the Startup shortcut points at it, `--test-toast` from the exe works, and the real 30 % toast fired at 26 % on start. PyInstaller warns about optional `winrt.windows.*` cross-references that win11toast does not need. **Verified by Chris 2026-10-17:** after a reboot at 14:36:19 the exe started from the Startup shortcut at 14:39:31 (real `%APPDATA%\HS80Widget\widget.log`), showed 26 % two seconds later and later "25 % charging". **M4 done.**

> **Debugging note.** Programs launched from the Claude desktop session run inside its packaged-app sandbox, which redirects `%APPDATA%` to a private copy under `AppData\Local\Packages\Claude_…\LocalCache\Roaming`. The widget started by Windows writes the real `C:\Users\<you>\AppData\Roaming\HS80Widget\`. From the session, read the real files via `\localhost\C$\Users\<you>\AppData\Roaming\HS80Widget\` and start the exe from Explorer or the Startup shortcut when the real environment matters.

### M5 — Hardening and soak (est. 2 h spread over days)

- [x] Capture a charging reading (2026-10-16 15:25, `charge_capture_log.txt`): 0x10 = 1 while on USB-C, 2 on battery. Level jumps while charging (43 → 72 % in 3 min), so show the charging glyph and treat the number as approximate while charging.
- [ ] Re-check the one miss from 14:32 (three plugged-in polls read 2) during the soak; `charge_raw` is now in every state-change log line
- [x] Low-battery toast no longer repeats on restart: the time of the last toast per threshold is kept in `config.json` (`last_alerts`) and a threshold toasted within the last 2 h starts pre-armed (2026-10-17 14:51 build)
- [x] Headset off → ASLEEP (seen 2026-10-16 16:09); receiver unplugged → NO_DONGLE, replug recovers in 15 s (2026-10-16 15:44)
- [ ] Headset out of range: not exercised; same ASLEEP path as headset off
- [ ] PC sleep → resume: the 2026-10-17 15:00 attempt only entered Away Mode (Kernel-Power 59, no 42/107), so the PC never suspended. Decision (Chris, 2026-10-17): leave it to normal use; the hourly heartbeat and the state log will show if a resume breaks polling
- [x] iCUE closed: works. iCUE was quit 14:53–15:00 on 2026-10-17; the widget kept reading every minute (108–109 ms vs 125 ms with iCUE open) and iCUE's per-minute warning trace resumed at 2/min when it came back
- [ ] 24 h soak **restarted 2026-10-17 15:20:14** with the configurable-ids build (first started 14:51:10; installed exe, real environment, 60 s interval). The widget logs an hourly `heartbeat:` line (uptime, polls, OK polls, RSS). Evaluate with `python soak_report.py` (reads the real log via `\localhost\C$` and counts iCUE's warning lines per hour). Pass = memory flat, ~60 polls/h, no ERROR, ~120 iCUE lines/h
- Observation: the headset's gauge is noisy for minutes after a plug/unplug (68 → 56 → 42 → 31 % within 3 min of unplugging; 31 → 69 % in 4 min of charging). The widget shows the raw value; if it bothers in use, smooth lightly while charging
- [ ] Optional: ask HeadsetControl / hs80-max-control whether the interface-3 finding is worth an issue (not blocking)

## Testing approach

- **Unit tests offline** with `FakeDevice` that returns the exact captured replies from
  RESULTS.md (`01 01 02 00 c2 01 …`), plus the failure shapes: no reply, `01 F0` NAK, value
  0, value 2710 (headset ID), error byte ≠ 0.
- **Hardware checklist** (manual, in README) run at the end of M2, M3, M5.
- **No automated hardware tests in CI**; there is no CI, this is a single-PC tool.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Receiver control endpoint stalls (OpenLinkHub 539) | timed reads; any failure closes the handle and reopens on the next poll (15 s, backing off to the poll interval); an absent receiver is NO_DONGLE and looked for every 15 s |
| Reply does not echo the property; another app's answer could parse as a level | accept only `[2]==02,[3]==00`, value 1..1000; read charge right after battery and sanity-check it is 0/1/2; drain stale reports before each request |
| iCUE log noise (1 line per reply) | 2 requests per poll, default 60 s interval; documented |
| Headset asleep gives 0 / ID values | retry 3×, then `ASLEEP` with last known % in tooltip, never show 0 % |
| pystray + tkinter thread rules | threading model fixed above; poller never touches UI |
| PyInstaller misses winrt / hidapi binaries | explicit hidden imports; smoke-test the exe in M4 |
| Phase 0 ran only ~12 min total | M5 soak before calling v1 done |

## Decisions from Chris (2026-10-16)

| Question | Answer |
|---|---|
| Default poll interval | **60 s** |
| Low-battery toasts | **30 % and 10 %** (first warning at 30 %, urgent at 10 %) |
| UI order | **Tray icon first** (v1), floating window in v2 |
| Mic-mute state | **Not in v1**; battery and charging only |

## Added after M4 (2026-10-17, Chris's request)

- **Configurable receiver ids.** `config.json` now has `vendor_id` (default `0x1B1C`) and `product_ids`
  (default `["0x0A97", "0x2A08", "0x2A02"]` per Chris's decision 2026-10-17, first present id wins);
  `python -m hs80 --vid/--pid`. Discovery still selects the collection by usage page FF42 / usage 0001,
  never by interface. Only 0x0A97 is verified; 0x2A08 Void Wireless v2 and 0x2A02 Virtuoso Max come from
  HeadsetControl's same device class. Tests in `tests/test_ids.py`. Installed 2026-10-17 (soak clock restarted).

## Not in scope

- Windows 11 Widgets Board integration (needs MSIX + Windows App SDK).
- Changing any headset setting (sidetone, EQ, lighting, sleep timer); the widget is read-only.
- Bluetooth mode of the headset (different transport, not reverse-engineered here).
- iCUE SDK fallback (`poc_icue.py` stays in the folder as an experiment).
