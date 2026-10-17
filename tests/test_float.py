"""Smoke tests for the floating window. They create a real (withdrawn) Tk root, so they
need a desktop session; they are skipped when Tk cannot open a display."""
import unittest

import config as C
from hs80.state import BatteryState, Status
from widget import icon as I

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.withdraw()
    HAVE_TK = True
except Exception:   # no display
    HAVE_TK = False
    _root = None


class StubApp:
    def __init__(self):
        self.cfg = C.Config(float_pos=(40, 50), click_through=False)
        self.saved = []
        self.toggled = 0

    def save_float_pos(self, pos):
        self.saved.append(pos)

    def toggle_float(self):
        self.toggled += 1

    def build_tk_menu(self, parent):
        return tk.Menu(parent, tearoff=0)


@unittest.skipUnless(HAVE_TK, "no Tk display")
class FloatWindowTests(unittest.TestCase):
    def setUp(self):
        from widget.float import FloatWindow
        self.app = StubApp()
        self.root = tk.Toplevel(_root)
        # FloatWindow expects a Tk-like window with the same attribute API; Toplevel has it
        self.win = FloatWindow(self.root, self.app)

    def tearDown(self):
        self.root.destroy()

    def test_renders_every_sample_state(self):
        for name, st in I.sample_states().items():
            self.win.update(st)
            self.assertGreater(len(self.win.canvas.find_all()), 2, name)
        self.win.update(BatteryState(status=Status.OK, percent=0.4, charging=False))
        self.win.update(BatteryState(status=Status.OK, percent=100.0, charging=True))

    def test_show_positions_from_config_and_hide(self):
        self.win.show()
        self.root.update_idletasks()
        self.assertTrue(self.win.visible)
        self.assertTrue(self.root.geometry().endswith("+40+50"))
        self.win.hide()
        self.assertFalse(self.win.visible)

    def test_default_position_is_top_right(self):
        self.app.cfg = self.app.cfg.with_(float_pos=None)
        x, y = self.win._clamped_pos(None)
        self.assertEqual(y, 24)
        self.assertEqual(x, self.root.winfo_screenwidth() - self.win.w - 24)

    def test_offscreen_position_is_clamped(self):
        x, y = self.win._clamped_pos((99999, -500))
        self.assertLessEqual(x, self.root.winfo_screenwidth() - self.win.w // 2)
        self.assertEqual(y, 0)

    def test_double_click_toggles_and_drag_saves(self):
        class E:
            pass
        self.win._double(E())
        self.assertEqual(self.app.toggled, 1)
        self.win.show()
        self.root.update_idletasks()
        e = E(); e.x_root, e.y_root = 100, 100
        self.win._press(e)
        e2 = E(); e2.x_root, e2.y_root = 130, 120
        self.win._motion(e2)
        self.root.update_idletasks()
        self.win._release(e2)
        self.assertEqual(len(self.app.saved), 1)


class ConfigClickThroughTests(unittest.TestCase):
    def test_click_through_default_and_coercion(self):
        self.assertFalse(C.Config().click_through)
        self.assertTrue(C._coerce({"click_through": 1}).click_through)
        self.assertFalse(C._coerce({"show_float": "", "click_through": None}).show_float)


if __name__ == "__main__":
    unittest.main()
