"""Start-with-Windows via a shortcut in the user's Startup folder.

Target is the current interpreter's pythonw.exe running main.py (the packaged exe later).
The .lnk is created through WScript.Shell in PowerShell so no extra Python package is needed.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys

log = logging.getLogger("hs80.autostart")

SHORTCUT_NAME = "HS80 Battery Widget.lnk"
STARTUP_DIR = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
SHORTCUT_PATH = os.path.join(STARTUP_DIR, SHORTCUT_NAME)
HERE = os.path.dirname(os.path.abspath(__file__))


def _target() -> tuple[str, str, str]:
    """(exe, args, workdir) for the shortcut."""
    if getattr(sys, "frozen", False):            # PyInstaller build
        return sys.executable, "", os.path.dirname(sys.executable)
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.isfile(pyw) else sys.executable
    return exe, f'"{os.path.join(HERE, "main.py")}"', HERE


def is_enabled() -> bool:
    return os.path.isfile(SHORTCUT_PATH)


def _ps(script: str) -> None:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"powershell exit {r.returncode}")


def enable() -> None:
    exe, args, workdir = _target()
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
        "$s.TargetPath = '{exe}'; $s.Arguments = '{args}'; $s.WorkingDirectory = '{wd}';"
        "$s.Description = 'HS80 Max battery tray widget'; $s.Save()"
    ).format(lnk=SHORTCUT_PATH.replace("'", "''"), exe=exe.replace("'", "''"),
             args=args.replace("'", "''"), wd=workdir.replace("'", "''"))
    _ps(script)
    log.info("autostart enabled: %s -> %s %s", SHORTCUT_PATH, exe, args)


def disable() -> None:
    try:
        os.remove(SHORTCUT_PATH)
        log.info("autostart disabled")
    except FileNotFoundError:
        pass


def current_target() -> str | None:
    """TargetPath of the existing shortcut, or None."""
    if not is_enabled():
        return None
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        "(New-Object -ComObject WScript.Shell).CreateShortcut('%s').TargetPath" % SHORTCUT_PATH.replace("'", "''")],
                       capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.stdout.strip() or None


def refresh_if_stale() -> bool:
    """Exe only: if autostart is on but the shortcut points at something else (the venv version,
    or an exe that moved), rewrite it for this exe. True if rewritten. Never runs from the venv,
    so starting the dev version does not steal the Startup entry back from the installed exe."""
    if not getattr(sys, "frozen", False) or not is_enabled():
        return False
    exe, _, _ = _target()
    cur = current_target()
    if cur and os.path.normcase(cur) == os.path.normcase(exe):
        return False
    log.info("autostart shortcut pointed at %s; rewriting for %s", cur, exe)
    enable()
    return True


def toggle() -> bool:
    if is_enabled():
        disable()
    else:
        enable()
    return is_enabled()
