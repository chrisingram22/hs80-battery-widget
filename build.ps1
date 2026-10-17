<#
.SYNOPSIS
  Build HS80BatteryWidget.exe (one file, no console) with PyInstaller.

.USAGE
  .\build.ps1            build to .\dist\HS80BatteryWidget.exe
  .\build.ps1 -Install   build, then copy to %LOCALAPPDATA%\Programs\HS80BatteryWidget\ and start it
  .\build.ps1 -Clean     remove build\, dist\ and the .spec first

  Enable "Start with Windows" from the widget's tray menu afterwards; the shortcut will point
  at the installed exe. If a shortcut from the venv version exists, the exe rewrites it on start.
#>
param([switch]$Install, [switch]$Clean)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "venv not found: $py  (python -m venv .venv; pip install -r requirements.txt pyinstaller)" }

if ($Clean) { Remove-Item -Recurse -Force build, dist, HS80BatteryWidget.spec -ErrorAction SilentlyContinue }

# a running instance locks its exe: stop one running from dist\ (plain build) or any instance (-Install)
$distExe = Join-Path $PSScriptRoot "dist\HS80BatteryWidget.exe"
Get-Process HS80BatteryWidget -ErrorAction SilentlyContinue |
  Where-Object { $Install -or ($_.Path -and ($_.Path -ieq $distExe)) } | Stop-Process -Force
Start-Sleep -Milliseconds 800

& $py -m widget.icon assets | Out-Null     # refresh assets/app.ico and icon_sheet.png (README)
Get-ChildItem assets\icon_*.png | Where-Object Name -ne "icon_sheet.png" | Remove-Item -ErrorAction SilentlyContinue

$args = @(
  "-m", "PyInstaller", "--noconfirm", "--clean",
  "--onefile", "--noconsole",
  "--name", "HS80BatteryWidget",
  "--icon", "assets\app.ico",
  "--hidden-import", "win11toast",
  "--collect-submodules", "winrt",          # winrt is a namespace package of many .pyd modules
  "--collect-binaries", "winrt",
  "--collect-data", "winrt",
  "--hidden-import", "hid",                 # cython-hidapi extension
  "--hidden-import", "pystray._win32",
  "--hidden-import", "PIL._tkinter_finder",
  "--exclude-module", "tests",
  "main.py"
)
& $py @args
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }

$exe = Join-Path $PSScriptRoot "dist\HS80BatteryWidget.exe"
$size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "built $exe ($size MB)"

if ($Install) {
  $dest = Join-Path $env:LOCALAPPDATA "Programs\HS80BatteryWidget"
  New-Item -ItemType Directory -Force $dest | Out-Null
  Copy-Item $exe (Join-Path $dest "HS80BatteryWidget.exe") -Force
  Write-Host "installed to $dest"
  # launch through Explorer so the widget runs in the normal user environment even when this
  # script is run from a sandboxed/packaged host (which would redirect %APPDATA%)
  Start-Process explorer.exe -ArgumentList ("`"" + (Join-Path $dest "HS80BatteryWidget.exe") + "`"")
  Write-Host "started. Use the tray menu to enable 'Start with Windows'."
}
