"""Floating always-on-top mini window (v2): battery glyph + percentage.

Runs on the Tk main thread. Borderless, draggable, position persisted, right-click menu
shared with the tray (via the App controller), double-click hides it. Optional click-through
(WS_EX_TRANSPARENT) so it never gets in the way; then it can only be managed from the tray.
"""
from __future__ import annotations

import ctypes
import logging
import tkinter as tk
from tkinter import font as tkfont

import config as C
from hs80.state import BatteryState, Status
from widget.icon import AMBER, BOLT, GREEN, GREY, RED, band_colour

log = logging.getLogger("hs80.float")

KEY = "#010203"               # colour key made transparent (never used in artwork)
BG = "#1e1e22"
FG = "#f4f4f4"
DIM = "#a0a0a8"
ALPHA = 0.94
BASE_W, BASE_H = 150, 56

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
GA_ROOT = 2


def _hex(rgba) -> str:
    return "#%02x%02x%02x" % rgba[:3]


class FloatWindow:
    def __init__(self, root: tk.Tk, app):
        self.root = root
        self.app = app
        self.state = BatteryState()
        self.visible = False
        self._drag = None
        self._moved = False

        self.s = max(1.0, root.winfo_fpixels("1i") / 96.0)      # DPI scale
        self.w, self.h = int(BASE_W * self.s), int(BASE_H * self.s)

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", ALPHA)
        root.attributes("-transparentcolor", KEY)
        root.configure(bg=KEY)
        root.title("HS80 Max battery")

        self.canvas = tk.Canvas(root, width=self.w, height=self.h, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack()
        self.big = tkfont.Font(family="Segoe UI", size=int(16), weight="bold")
        self.small = tkfont.Font(family="Segoe UI", size=int(8))

        for seq, fn in (("<ButtonPress-1>", self._press), ("<B1-Motion>", self._motion),
                        ("<ButtonRelease-1>", self._release), ("<Double-Button-1>", self._double),
                        ("<Button-3>", self._popup)):
            self.canvas.bind(seq, fn)
        root.withdraw()
        self._draw()

    # ---- visibility & position ------------------------------------------------------
    def show(self) -> None:
        x, y = self._clamped_pos(self.app.cfg.float_pos)
        self.root.geometry(f"{self.w}x{self.h}+{x}+{y}")
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.visible = True
        self.root.after(50, lambda: self.set_click_through(self.app.cfg.click_through))

    def hide(self) -> None:
        self.root.withdraw()
        self.visible = False

    def _clamped_pos(self, pos) -> tuple[int, int]:
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        if pos is None:
            return sw - self.w - 24, 24          # top-right corner by default
        x, y = pos
        return max(-self.w // 2, min(int(x), sw - self.w // 2)), max(0, min(int(y), sh - self.h // 2))

    def _hwnd(self) -> int:
        return ctypes.windll.user32.GetAncestor(self.root.winfo_id(), GA_ROOT)

    def set_click_through(self, on: bool) -> None:
        try:
            u = ctypes.windll.user32
            hwnd = self._hwnd()
            style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if on:
                style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
            else:
                style &= ~WS_EX_TRANSPARENT
            u.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception as e:
            log.warning("click-through failed: %s", e)

    # ---- mouse --------------------------------------------------------------------
    def _press(self, e):
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())
        self._moved = False

    def _motion(self, e):
        if self._drag:
            self.root.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")
            self._moved = True

    def _release(self, e):
        if self._drag and self._moved:
            self.app.save_float_pos((self.root.winfo_x(), self.root.winfo_y()))
        self._drag = None

    def _double(self, e):
        self.app.toggle_float()

    def _popup(self, e):
        m = self.app.build_tk_menu(self.root)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    # ---- drawing --------------------------------------------------------------------
    def update(self, st: BatteryState) -> None:
        self.state = st
        self._draw()

    def _rrect(self, x0, y0, x1, y1, r, **kw):
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
               x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def _draw(self) -> None:
        c, s, st = self.canvas, self.s, self.state
        c.delete("all")
        self._rrect(0, 0, self.w - 1, self.h - 1, 12 * s, fill=BG, outline="#3a3a40")

        # battery glyph
        gx0, gy0, gx1, gy1 = 12 * s, 16 * s, 56 * s, 40 * s
        self._rrect(gx0, gy0, gx1, gy1, 4 * s, fill="", outline=DIM, width=max(1, int(2 * s)))
        c.create_rectangle(gx1, gy0 + 7 * s, gx1 + 4 * s, gy1 - 7 * s, fill=DIM, outline=DIM)

        ok = st.status is Status.OK and st.percent is not None
        if ok:
            frac = max(0.0, min(1.0, st.percent / 100.0))
            colour = _hex(band_colour(st.percent))
            inner = 3 * s
            fw = (gx1 - gx0 - 2 * inner) * frac
            if fw >= 1:
                c.create_rectangle(gx0 + inner, gy0 + inner, gx0 + inner + fw, gy1 - inner, fill=colour, outline=colour)
            if st.charging:
                bx, by = (gx0 + gx1) / 2, (gy0 + gy1) / 2
                pts = [bx + 2 * s, by - 10 * s, bx - 5 * s, by + 1 * s, bx - 0.5 * s, by + 1 * s,
                       bx - 3 * s, by + 10 * s, bx + 5 * s, by - 2 * s, bx + 0.5 * s, by - 2 * s]
                c.create_polygon(pts, fill=_hex(BOLT), outline="#000000")
            c.create_text(70 * s, self.h / 2 - 1 * s, text=f"{st.percent:.0f}%", anchor="w", fill=FG, font=self.big)
            if st.charging:
                c.create_text(self.w - 10 * s, self.h / 2 + 15 * s, text="charging", anchor="e", fill=DIM, font=self.small)
        else:
            if st.status is Status.ASLEEP:
                glyph, label = "zz", "headset asleep" + (f" · last {st.last_percent:.0f}%" if st.last_percent is not None else "")
            elif st.status is Status.NO_DONGLE:
                glyph, label = "–", "receiver not found"
            else:
                glyph, label = "!", "error · retrying"
            c.create_text((gx0 + gx1) / 2, (gy0 + gy1) / 2, text=glyph, fill=_hex(GREY), font=self.big)
            c.create_text(70 * s, self.h / 2 - 8 * s, text="HS80 Max", anchor="w", fill=FG, font=self.small)
            c.create_text(70 * s, self.h / 2 + 8 * s, text=label, anchor="w", fill=DIM, font=self.small)
