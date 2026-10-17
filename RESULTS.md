# HS80 Max Battery Widget — Phase 0 results

_Run 2026-10-16 on this PC (Windows 11 Education 10.0.26300), iCUE 5 running throughout._

## Verdict

**Build the widget on Path A (direct HID over the Slipstream receiver).** It returned the
battery level on the first attempt, every poll for 10 minutes succeeded, each read takes
about a quarter of a second, and iCUE kept working alongside it. The only side effect found
is a warning line in iCUE's own log for every reply our requests produce (details below).

**Path B (iCUE SDK) was not tested.** Chris declined the SDK download. `poc_icue.py` is
written and ready, but it needs `iCUESDK.x64_2019.dll` from the official
`iCUESDK_4.0.84.zip` (347 KB, CorsairOfficial/cue-sdk, released 2023-11-21) and no copy of
that DLL exists anywhere on this PC. It also turns out iCUE itself only shows **high /
medium / low** for the HS80 Max, not a number, so the SDK's `CDPI_BatteryLevel` would be
the only way to get a percentage from iCUE, and it is unverified whether iCUE reports a
real percentage for this headset through the SDK.

## Match against iCUE

| Source | Value at 13:21–13:33 |
|---|---|
| HID path (`poc_hid.py`) | **45.0 %** (raw uint16 450), stable across every read |
| iCUE UI (reported by Chris) | "medium" — iCUE gives no number for this headset |

45 % is consistent with "medium". An exact numeric cross-check is not possible from the
iCUE UI.

## Environment

| Item | Result |
|---|---|
| venv | `.venv` in this folder, Python 3.12.3 |
| Packages | `hidapi 0.15.0`, `pystray 0.19.5`, `pillow 12.3.0`, `win11toast 0.36.3` (see `requirements.txt`) |
| HID binding | The `hid` package (pyhidapi) installs but **cannot load**: it needs a separate `hidapi.dll`. Replaced with the `hidapi` wheel (cython-hidapi), which has hidapi compiled in. Both import as `hid` but the API differs: `hid.device()` + `open_path()`, `read(n, timeout_ms)`, `set_nonblocking()`. |
| Collections on 1B1C:0A97 | all on interface 3: Col01 000C/0001 (consumer), Col02 FF13/0001, Col03 000B/0005 (telephony), **Col04 FF42/0001 (control, used)**, Col05 FF42/0002 (notice channel) |
| Control path | `\\?\HID#VID_1B1C&PID_0A97&MI_03&Col04#8&2cca5b2e&0&0003#{4d1e55b2-f16f-11cf-88cb-001111000030}` |

## Protocol as actually sent (verified on hardware)

Selected by usage page 0xFF42 / usage 0x0001, never by interface or collection index.
All requests are one 64-byte output report whose first byte is the Windows report ID 0x02.
Windows accepted the 64-byte form directly (`hid.write` returned 64); the 65-byte form with
a leading 0x00 was never needed.

| Step | Bytes sent (first 4, rest zero to 64) | Reply (first 8 of 64) | Meaning |
|---|---|---|---|
| receiver firmware query | `02 08 02 13` | `01 00 02 00 00 0a 42 00` | route 0 = receiver, cmd 02, err 00, fw 0.10.66? |
| receiver heartbeat | `02 08 02 12` | `01 00 02 00 97 0a 00 00` | value 0x0A97 = receiver PID |
| headset heartbeat | `02 09 02 12` | `01 01 02 00 96 0a 00 00` | route 1 = headset, value 0x0A96 = headset PID; a reply here = headset awake |
| **battery** | `02 09 02 0f` | `01 01 02 00 c2 01 00 00` | **0x01C2 = 450 → 45.0 %** |
| charge state | `02 09 02 10` | `01 01 02 00 02 00 00 00` | 2 while discharging at 45 % |
| mic mute | `02 09 02 a6` | `01 01 02 00 01 00 00 00` | 1 = muted, 0 = live; changed with the boom during the poll |

Reply layout: `[0]` report ID 0x01, `[1]` route (0 receiver, 1 headset), `[2]` cmd echo
(0x02 = get), `[3]` error (0x00 = OK), `[4..5]` little-endian uint16 value. A reply
beginning `01 F0` is a NAK (hs80-max-control). The reply echoes the command but **not the
property**, so a reply must be matched to the request by order; with another app on the
same dongle a foreign answer could parse as a level (HaloBattery guards against this by
asking twice and requiring agreement).

Timing (handshake + battery, opening and closing the handle each time):

| Measure | Value |
|---|---|
| handshake (3 writes, 3 reads) | ~210 ms |
| handshake + battery | 208–234 ms |
| handshake + battery + charge + mic | 208–259 ms |
| battery only, no handshake | 58–68 ms |
| hang / timeout | none; every read answered within one 500 ms timed read |

## 10-minute poll alongside iCUE

See `poll_log.txt` (30 s interval, 10 min, handle opened and closed per poll).

| Measure | Result |
|---|---|
| reads | **21 / 21 valid**, 13:23:12 – 13:33:20 |
| value | 45.0 % on every read (raw `01 01 02 00 c2 01`), charge state 2 on every read |
| round trip (handshake + battery + charge + mic) | 208–259 ms typical, one outlier of 1243 ms (read 12, still a valid reply) |
| hangs / timeouts / exceptions | none; 0 reads over 5 s |
| mic mute | tracked live: muted on reads 1–6, unmuted from read 7 on (boom flipped during the poll) |

**iCUE during the poll.** iCUE kept running; no error, disconnect or crash entry in its log
(`%LOCALAPPDATA%\Corsair\Logs\CUE5\2026-10-12_12-45-48.875.log`), nothing in `CrashData`.
iCUE does see our traffic: because Windows delivers HID input reports to every open handle,
each reply our request triggers reaches iCUE's "bragi" protocol dispatcher, which logs one
**warning** per reply:

```
W cue.mod.bragi.hid_report_dispatcher: No command candidates for unexpected reply
  bragiprotocol::CommandId::GetProperty for subdevice 0 "HS80 MAX WIRELESS DONGLE(USB\VID_1B1C&PID_0A97\...)"
```

Six lines per poll (one per request: 2 to the receiver = subdevice 0, 4 to the headset =
subdevice 1). Harmless, but at 60 s polling that is ~8,600 log lines a day, so the widget
should send as few requests per poll as possible.

**Battery read without the handshake** (`--no-wake --no-extras`, hs80-max-control style,
run twice right after the poll): works. Reply `01 01 02 00 c2 01` = 45.0 % both times,
58 ms and 68 ms end to end, and **exactly one** iCUE warning line per read instead of six.
Caveat: the headset was already awake from the preceding poll; whether a single battery
request also wakes a sleeping headset was not tested. Phase 1 should try the bare battery
request first and fall back to the three-write handshake only when it gets no valid reply.

Whether lighting or settings were visibly disturbed could not be checked from this session
(no screen access was requested); Chris saw nothing unusual while answering during the
poll. Nothing in iCUE's log suggests a settings reload or a device reset.

## Corrections to PLAN.md

1. **Scale is tenths, not hundredths.** The uint16 in reply bytes 4–5 is 0–1000 =
   tenths of a percent. HeadsetControl 4.1.0 (`corsair_void_v2w.hpp`:
   `battery_level_vendor_max = 1000`, `level = value / 10`), hs80-max-control
   (`BatteryReader.cs`: `/ 10.0`) and HaloBattery (`LEVEL_MAX = 1000`, `// 10`) all agree,
   and the hardware confirmed it (450 → 45 %). "0 or > 10000 = unknown" must become
   **"0 or > 1000 = unknown"**, otherwise a bogus value like 2710 (headset ID) would show as 271 %.
2. **HeadsetControl 4.x selects this device by interface_id = 4, not usage page** — that is
   the HeadsetControl behaviour HaloBattery copied and that breaks on Windows (here the
   control collection is interface 3). Our selection by FF42/0001 is right; keep it.
   hs80-max-control matches on `mi_04` + `col01` in the path **and** usage page FF42 in the
   report descriptor; on this PC the path is `MI_03&Col04`, so its path filter would also
   fail here. Usage page/usage is the only portable selector.
3. **Request framing on Windows:** 64 bytes starting with report ID 0x02 (`02 09 02 0F …`).
   HeadsetControl's 65-byte `00 02 09 02 0F` is the hidapi-on-Linux form of the same frame.
4. **Charge state exists and is cheap:** property 0x10 on the headset. Observed 2 while
   discharging. hs80-max-control labels 0 = discharging, 1 = charging, 2 = "charge complete",
   but HaloBattery measured 1 = charging, 2 = on battery on the same protocol family, and
   2-at-45 % here supports HaloBattery's reading. Treat **1 = charging, anything else = not
   charging** until a charging capture is made. Mic mute is property 0xA6 (1 = muted, verified live).
5. **Rate limits to keep:** ≥ 50 ms between writes (hs80-max-control `MinWriteGapMs`),
   ≥ 2 s between battery reads (its `RateGate`). HeadsetControl's `wakeDevice` handshake
   is the non-software-mode one; never send the software-mode set (`02 08 01 03 00 02`),
   which would take control away from iCUE and cause the audible pop.
6. **iCUE only shows high/medium/low** for this headset, so the widget cannot be validated
   against an iCUE number; validate against charge behaviour over time instead.
7. **Open question from PLAN.md "does direct HID polling disturb iCUE":** no functional
   disturbance seen; the cost is the warning lines in iCUE's log.
8. **Headset off / asleep** was not exercised (the headset stayed on). The code path for it
   exists (no heartbeat reply → unknown; value 0 or > 1000 → retry then unknown) and comes
   straight from HeadsetControl, but it is untested on this hardware.

## Addendum 2026-10-16 15:05 (Milestone 1 watch)

`python -m hs80 --watch --interval 60 --minutes 30`: 30/30 polls OK, 108–141 ms, two requests per poll.
The headset was plugged in over USB-C between 14:32:14 and 14:35:11 (iCUE log). Level read 42 → 53 → 64 → 62 → 53 → 47 → 46 → 45 %
across that window and after it, i.e. the gauge rises while on external power and settles back afterwards.
Property 0x10 did **not** read 1 during the three polls while plugged in, so "1 = charging" (hs80-max-control /
HaloBattery) is unconfirmed on the HS80 Max over the dongle. Raw 0x10 values were not logged in that run; the
CLI now prints `chg_raw` so the next USB-C capture settles it.

## Addendum 2026-10-16 15:28 (USB-C charging capture)

`python -m hs80 --watch --interval 10 --minutes 3 --verbose` → `charge_capture_log.txt`, 18/18 polls OK.
Headset plugged into the PC over USB-C at 15:25:48.9 (iCUE: `ExternalPowerSourcePresent` change).

| Time | battery reply | 0x10 reply | Meaning |
|---|---|---|---|
| 15:25:38 | `01 01 02 00 ae 01` = 43.0 % | `01 01 02 00 02 00` | before plug-in: **2 = on battery** |
| 15:25:49 → 15:28:30 | 43.0 → 46.0 → 59.0 → 72.0 % | `01 01 02 00 01 00` on every poll | **1 = charging**, flipped within 1 s of plug-in |

So property 0x10 is the charge state: **1 = charging, 2 = on battery** (HaloBattery's mapping; hs80-max-control's
"2 = charge complete" is wrong). The level reported while charging climbs in jumps (43 → 72 % in under 3 min), so the
gauge is not trustworthy while on external power and the widget should show a charging glyph rather than the
number as a trend. **Unexplained:** during the 14:31–15:01 watch, three polls while the headset was on USB-C
(14:32:27–14:34:27) read "not charging"; the raw 0x10 value was not logged then. Watch for it in the M5 soak.

## Files

| File | Purpose |
|---|---|
| `poc_hid.py` | Path A proof of concept. `python poc_hid.py` = one verbose read; `--poll 30 --minutes 10` = poll; `--no-wake` skips the handshake; `--no-extras` skips charge/mic. |
| `poc_icue.py` | Path B proof of concept (ctypes over iCUE SDK v4). Untested: needs the SDK DLL under `.\sdk\` or `--dll`. |
| `poll_log.txt` | Raw output of the 10-minute poll. |
| `requirements.txt` | Frozen venv packages. |

## Recommendation for Phase 1

- Path A only. Drop Path B from the plan unless iCUE-side lighting/settings problems show
  up in daily use; keep `poc_icue.py` around as the fallback experiment.
- Per poll: open handle → battery (0x0F) → charge (0x10) → close; send the three-write
  handshake only when the bare request gets no valid reply. Skip the mic read. That is 2
  iCUE log lines per poll instead of 6.
- Validate replies as `[2] == 0x02 and [3] == 0x00`, value in 1..1000, retry up to 3×, then
  show "unknown" (never 0 %).
- Keep hidapi's timed reads (500 ms) so a stalled receiver can never hang the poller; on
  repeated timeouts close and re-enumerate (OpenLinkHub issue 539 stalls).
