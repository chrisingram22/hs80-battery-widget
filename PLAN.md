# HS80 Max Battery Widget — Viability Assessment & Plan

> **Update 2026-10-16:** Phase 0 is done. See **RESULTS.md** for what was measured (Path A works; the battery value is in *tenths* of a percent, not hundredths; use the `hidapi` wheel, not `hid`) and **DEV_PLAN.md** for the development plan that replaces the "Phased plan" section below.

_Assessed 2026-10-16 on this PC (Windows 11 Education 10.0.26300)._

## Verdict

**Viable, low-to-moderate effort.** The headset is reachable from this PC right now and there are
two independent, documented ways to read its battery level. A working proof of concept should take
an hour or two; a polished tray/floating widget a few evenings.

## What was confirmed on this machine

| Item | Finding |
|---|---|
| Headset | CORSAIR HS80 MAX WIRELESS, connected over the Slipstream USB receiver (not Bluetooth) |
| USB IDs | VID `1B1C`, PID `0A97` ("CORSAIR HS80 MAX WIRELESS Gaming Receiver") |
| Control HID collection | Interface 3, collection 4: **Usage Page `0xFF42`, Usage `0x0001`** (collection 5 = FF42/0002) |
| iCUE | iCUE 5 installed at `C:\Program Files\Corsair\Corsair iCUE5 Software\` and running |
| Windows battery property | **Not exposed.** The dongle shows no `DEVPKEY_Bluetooth_Battery`; Windows itself cannot show this battery |
| Toolchains | Python 3.12.3 (no HID packages yet), Node, GitHub CLI. .NET SDK is only 3.1 (end of life). No Rainmeter. |

## Two data paths

### Path A — Direct HID over the dongle (no iCUE dependency)
Vendor protocol reverse-engineered by HeadsetControl (v4.0.0 added HS80 Max), BrokeSkill/hs80-max-control
(C#, MIT) and Rexarn's HeadsetControl fork. Summary of the protocol as those projects describe it:

- Open the HID collection with usage page `0xFF42`, usage `0x0001` (matches COL04 above).
- Requests are 64–65 byte output reports, Windows report ID `0x02`, layout `02 <endpoint> 02 <cmd> ...`.
  Endpoint `0x08` = receiver, `0x09` = headset behind it. Battery command = `0x0F`.
- Wake handshake first: receiver firmware query, receiver heartbeat, headset heartbeat.
- Reply bytes 4–5 = little-endian uint16, battery in **hundredths of a percent** (0–10000).
  A value of 0 or >10000 means "headset asleep / not answering" and must be treated as unknown, not 0%.
- No charging flag in that reply; charging state needs a separate property (hs80-max-control reads it).

Pros: exact percentage, works even if iCUE is closed, tiny footprint.
Risks: coexistence with iCUE is **untested by every one of those authors**. Windows allows shared HID opens,
so it should work, but replies may interleave; need a correlation/retry layer. The receiver's control endpoint is
also reported to stall periodically (OpenLinkHub issue 539), so reconnect logic is mandatory.
HaloBattery's HS80 Max support is reported broken because it picked interface *number* 4 instead of
usage page FF42 — we must select by usage page, not interface index.

### Path B — Official iCUE SDK v4 (CorsairReadDeviceProperty)
`CDPI_BatteryLevel` (property 9) returns 0–100 for wireless devices; `CDT_Headset` is a documented device type.

Pros: official, stable, no reverse-engineered bytes, charging status also available.
Cons: requires iCUE running and third-party control enabled in iCUE settings; integer percent only;
C/C++ DLL (ctypes from Python or P/Invoke from C#); client must run at the same privilege level as iCUE.

**Recommendation:** build Path A as primary with Path B as an automatic fallback (or vice-versa after
the proof of concept shows which is more reliable alongside iCUE on this PC).

## Widget delivery options

| Option | Effort | Notes |
|---|---|---|
| **System tray icon** with % drawn into the icon, tooltip, low-battery toast | Low | Recommended v1. Always visible, zero screen real estate. |
| **Floating always-on-top mini window** (true "widget" look, draggable, remembers position) | Low–Med | Recommended v2, toggled from the tray menu. |
| Windows 11 Widgets Board panel | High | Needs an MSIX-packaged app + Windows App SDK widget provider. Not worth it for v1. |
| Rainmeter skin | Low | Rainmeter is not installed; would still need a helper exe for the HID read. Skip. |

## Tech stack recommendation

**Python 3.12** (already installed): `hid` (hidapi) for Path A, `ctypes` against `iCUESDK.x64_2019.dll` for Path B,
`pystray` + `Pillow` for the tray icon, `tkinter` (stdlib) for the floating window, `win11toast` for notifications,
`PyInstaller` to ship a single `.exe` with a Startup-folder shortcut.

Alternative: C# / WPF (what hs80-max-control uses) gives a nicer widget and smaller RAM, but needs a .NET 8+ SDK install first.
Decide after Phase 0.

## Phased plan

**Phase 0 — Proof of concept (1–2 h)**
1. `pip install hid pystray pillow win11toast` into a venv in this folder.
2. Script `poc_hid.py`: enumerate VID 1B1C / PID 0A97, open the FF42/0001 collection, run the wake handshake,
   send cmd `0x0F`, print the percentage. Test with iCUE running and with iCUE closed.
3. Script `poc_icue.py`: connect to the iCUE SDK, find the headset, read `CDPI_BatteryLevel`.
4. Record which path works reliably alongside iCUE; note sleep/wake behaviour of the headset.
   **Exit criterion:** a number that matches what iCUE shows, refreshed every 30 s for 10 min without hanging.

**Phase 1 — Tray widget (v1)**
- Background poller (configurable interval, default 60 s) with retry/reconnect and "unknown" state.
- Tray icon rendering the percentage as text + colour band (green/amber/red), tooltip with details,
  right-click menu: refresh, interval, start with Windows, quit.
- Toast at 20 % and 10 %; detect disconnect / headset powered off.

**Phase 2 — Floating widget (v2)**
- Small borderless always-on-top window showing % and a battery glyph, draggable, position persisted,
  click-through optional, toggle from tray.

**Phase 3 — Packaging**
- PyInstaller one-file build, Startup shortcut, `config.json`, README with the protocol notes above.

## Open questions to settle during Phase 0
- Does direct HID polling disturb iCUE (dropped settings, lighting flicker)? If yes, use Path B only.
- Does the headset report while charging over USB-C, and does the dongle answer when the headset is off?
- Is charging state worth a second HID query for v1?

## Sources
- HeadsetControl 4.0.0 release notes ("Add HS80 Max support", PR 537) — github.com/Sapd/HeadsetControl
- Rexarn/Headset-control-with-HS80-Wireless-FULLY-WORKING (FF42 Col04 on interface 3, report ID 0x02)
- BrokeSkill/hs80-max-control (C#/.NET, usage page 0xFF42, battery property 0x0F, MIT)
- HeyOkay/HaloBattery PR 32 (65-byte request layout, hundredths-of-percent reply, wake handshake)
- jurkovic-nikola/OpenLinkHub issue 539 (receiver control endpoint stalls)
- Corsair iCUE SDK v4 API reference (CDPI_BatteryLevel, CDT_Headset, iCUE must be running)
