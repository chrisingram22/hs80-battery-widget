# HS80 Max Battery Widget

A small Windows tray widget that shows the battery percentage of a Corsair HS80 Max wireless
headset, read directly from its Slipstream USB receiver. It works whether or not iCUE is
running, shows the number in the tray icon, optionally as a floating always-on-top window,
and sends a toast when the battery gets low.

![tray icons](assets/icon_sheet.png)

## Install

1. Build or download `HS80BatteryWidget.exe` (see *Build* below), or run `.\build.ps1 -Install`
   which copies it to `%LOCALAPPDATA%\Programs\HS80BatteryWidget\` and starts it.
2. Right-click the tray icon → **Start with Windows**. That creates
   `HS80 Battery Widget.lnk` in your Startup folder pointing at the exe you are running.
3. Optional: **Show floating widget** for the always-on-top mini window. Drag it where you
   like (position is remembered), double-click to hide, right-click for the same menu.
   **Floating widget click-through** makes it ignore the mouse; turn it off again from the tray.

Settings and the log live in `%APPDATA%\HS80Widget\` (`config.json`, `widget.log`).
Defaults: poll every 60 s, toasts at 30 % and 10 %.

## Uninstall

1. Tray icon → **Quit** (or end `HS80BatteryWidget.exe`).
2. Tray icon → untick **Start with Windows** first, or delete
   `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\HS80 Battery Widget.lnk`.
3. Delete `%LOCALAPPDATA%\Programs\HS80BatteryWidget\` and `%APPDATA%\HS80Widget\`.

## Tray icon

| Icon | Meaning |
|---|---|
| number over a green / amber / red band | battery level (≥ 50 / 30–49 / < 30 %) |
| small bolt | charging over USB-C |
| grey **zz** | receiver present, headset off or asleep (last level in the tooltip) |
| grey **–** | receiver not plugged in; checked again every 15 s |
| grey **!** | HID error; retried at 15 s, 30 s, then every poll |

## What it sends to the headset

Only *get* requests, never a setting. It opens the receiver's vendor HID collection
(VID `1B1C`, PID `0A97`, usage page `FF42`, usage `0001`) and, every poll, sends two 64-byte
reports and reads the replies:

| Request (first 4 of 64 bytes) | Reply (first 6 of 64) | Meaning |
|---|---|---|
| `02 09 02 0F` | `01 01 02 00 <lo> <hi>` | battery, uint16 LE in tenths of a percent (450 = 45.0 %) |
| `02 09 02 10` | `01 01 02 00 01/02 00` | 1 = charging, 2 = on battery |

If the battery request gets no usable answer (0, > 1000, or silence) it runs the same
wake handshake HeadsetControl uses (`02 08 02 13`, `02 08 02 12`, `02 09 02 12`) and
retries, then shows "asleep". Every read is a timed 500 ms read, so a stalled receiver can
never hang the widget. Details and captures: [RESULTS.md](RESULTS.md).

## Other Corsair Slipstream headsets

By default the widget looks for three receivers that HeadsetControl drives with the same
protocol, in this order: `0x0A97` HS80 Max (verified here), `0x2A08` Void Wireless v2 and
`0x2A02` Virtuoso Max (both untested with this widget). The first id that is plugged in wins.
The list lives in `%APPDATA%\HS80Widget\config.json`; edit it and restart the widget to add
another id or to pin a single one:

```json
  "vendor_id": "0x1B1C",
  "product_ids": ["0x0A97", "0x2A08", "0x2A02"]
```

Values may be hex strings or numbers. Find a receiver's id in Device Manager (Hardware Ids,
`VID_1B1C&PID_xxxx`). Try an id first from the command line:

```powershell
.\.venv\Scripts\python.exe -m hs80 --pid 0x2A08 --verbose
```

A different id is only half the story: the receiver must expose the vendor collection
(usage page `FF42`, usage `0001`) and answer the `02 09 02 0F` battery request as described above.
The widget never sends a *set*, so trying an unknown receiver is safe; the worst case is
"receiver not found" or "headset asleep".

## Known limitations

- **iCUE log noise.** Windows delivers HID input reports to every program that has the
  collection open, so iCUE sees our replies and writes one warning line per reply to its
  own log (`%LOCALAPPDATA%\Corsair\Logs\CUE5\…log`, "No command candidates for unexpected
  reply"). Two lines per poll; harmless, and the reason the default poll is 60 s.
- iCUE itself only shows high / medium / low for this headset; the widget's percentage is
  the headset's own 0.1 % gauge, which jumps around while charging over USB-C.
- Slipstream (USB receiver) mode only. Bluetooth mode is a different transport and is not read.
- Mic-mute state is readable (property `A6`) but not shown.

## Build

Requirements: Windows 10/11, Python 3.12.

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt pyinstaller
.\build.ps1            # -> dist\HS80BatteryWidget.exe (one file, ~21 MB)
.\build.ps1 -Install   # also copies to %LOCALAPPDATA%\Programs\HS80BatteryWidget and starts it
```

Run from source instead: `.\.venv\Scripts\pythonw.exe main.py`.
Command-line battery read: `.\.venv\Scripts\python.exe -m hs80` (`--watch`, `--json`, `--verbose`).
Tests: `.\.venv\Scripts\python.exe -m unittest discover -s tests -t .`

## Credits

Protocol knowledge from [Sapd/HeadsetControl](https://github.com/Sapd/HeadsetControl),
[BrokeSkill/hs80-max-control](https://github.com/BrokeSkill/hs80-max-control) and
[HeyOkay/HaloBattery](https://github.com/HeyOkay/HaloBattery); verified on hardware here
(see RESULTS.md). Built with `hidapi`, `pystray`, `Pillow`, `win11toast`, `tkinter`, `PyInstaller`.
