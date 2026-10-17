"""Render the tray icon: the percentage as big digits over a colour band.

Pure function of BatteryState -> PIL.Image, so it is unit-testable and can be previewed
by writing the images to PNG files (see tests/ and `python -m widget.icon`).
"""
from __future__ import annotations

import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from hs80.state import BatteryState, Status

SIZE = 64                      # rendered size; Windows scales it to the tray's 16/24/32 px
GREEN = (46, 204, 113, 255)
AMBER = (241, 196, 15, 255)
RED = (231, 76, 60, 255)
GREY = (140, 140, 140, 255)
BLUE = (52, 152, 219, 255)
WHITE = (255, 255, 255, 255)
BOLT = (255, 221, 0, 255)
SHADOW = (0, 0, 0, 160)

AMBER_BELOW = 50               # >= 50 green
RED_BELOW = 30                 # 30–49 amber, < 30 red (matches the first toast threshold)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuib.ttf",   # Segoe UI Bold
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\verdanab.ttf",
]


@lru_cache(maxsize=None)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def band_colour(percent: float | None) -> tuple[int, int, int, int]:
    if percent is None:
        return GREY
    if percent >= AMBER_BELOW:
        return GREEN
    if percent >= RED_BELOW:
        return AMBER
    return RED


def _text(draw: ImageDraw.ImageDraw, text: str, size: int, cx: float, cy: float, fill=WHITE) -> None:
    font = _font(size)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    x = cx - (right - left) / 2 - left
    y = cy - (bottom - top) / 2 - top
    # soft outline so the digits read on both light and dark taskbars
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, 1), (-1, 1), (1, -1)):
        draw.text((x + dx, y + dy), text, font=font, fill=SHADOW)
    draw.text((x, y), text, font=font, fill=fill)


def _bolt(draw: ImageDraw.ImageDraw) -> None:
    # small lightning bolt, top-right corner
    pts = [(56, 0), (48, 15), (54, 15), (50, 27), (62, 11), (56, 11), (60, 0)]
    draw.polygon(pts, fill=BOLT, outline=(0, 0, 0, 200))


def render(state: BatteryState, size: int = SIZE) -> Image.Image:
    """Icon for a state. Always returns an RGBA image of `size` px."""
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    band_h = 12
    colour = band_colour(state.percent if state.status is Status.OK else None)
    d.rounded_rectangle((2, SIZE - band_h - 2, SIZE - 3, SIZE - 3), radius=4, fill=colour)

    if state.status is Status.OK and state.percent is not None:
        pct = int(round(state.percent))
        text = str(pct)
        cx = SIZE / 2 - (3 if state.charging else 0)
        _text(d, text, 34 if pct < 100 else 26, cx, (SIZE - band_h) / 2 - 1)
        if state.charging:
            _bolt(d)
    elif state.status is Status.ASLEEP:
        _text(d, "zz", 30, SIZE / 2, (SIZE - band_h) / 2 - 1, fill=GREY)
    elif state.status is Status.NO_DONGLE:
        _text(d, "–", 36, SIZE / 2, (SIZE - band_h) / 2 - 1, fill=GREY)
    else:
        _text(d, "!", 36, SIZE / 2, (SIZE - band_h) / 2 - 1, fill=GREY)

    if size != SIZE:
        img = img.resize((size, size), Image.LANCZOS)
    return img


def app_icon(size: int = 256) -> Image.Image:
    """Application icon (exe / shortcut): a green battery glyph with a bolt, no digits."""
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((24, 72, 208, 184), radius=22, fill=(30, 30, 34, 255), outline=(220, 220, 220, 255), width=10)
    d.rounded_rectangle((208, 104, 232, 152), radius=6, fill=(220, 220, 220, 255))
    d.rounded_rectangle((42, 90, 160, 166), radius=12, fill=GREEN)
    d.polygon([(132, 60), (96, 136), (124, 136), (108, 200), (156, 112), (128, 112), (144, 60)],
              fill=BOLT, outline=(0, 0, 0, 220))
    return img.resize((size, size), Image.LANCZOS) if size != 256 else img


def write_ico(path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    app_icon().save(path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def sample_states() -> dict[str, BatteryState]:
    return {
        "ok_85": BatteryState(status=Status.OK, percent=85.0, charging=False),
        "ok_45": BatteryState(status=Status.OK, percent=45.0, charging=False),
        "ok_12": BatteryState(status=Status.OK, percent=12.0, charging=False),
        "ok_100_charging": BatteryState(status=Status.OK, percent=100.0, charging=True),
        "ok_73_charging": BatteryState(status=Status.OK, percent=73.0, charging=True),
        "asleep": BatteryState(status=Status.ASLEEP, last_percent=45.0),
        "no_dongle": BatteryState(status=Status.NO_DONGLE),
        "error": BatteryState(status=Status.ERROR, error="x"),
    }


if __name__ == "__main__":   # python -m widget.icon <out_dir>: write sample PNGs
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    sheet = Image.new("RGBA", (len(sample_states()) * 72, 72 * 2), (60, 60, 60, 255))
    for i, (name, st) in enumerate(sample_states().items()):
        im = render(st)
        im.save(os.path.join(out, f"icon_{name}.png"))
        sheet.paste(im, (i * 72 + 4, 4), im)
        sheet.paste(render(st, 32), (i * 72 + 20, 80), render(st, 32))
    sheet.save(os.path.join(out, "icon_sheet.png"))
    write_ico(os.path.join(out, "app.ico"))
    print("wrote", os.path.join(out, "icon_sheet.png"), "and app.ico")
