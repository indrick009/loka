"""DEMO ONLY — placeholder pictures, drawn rather than downloaded.

No real face and no photo taken from the internet: each picture is a coloured
card that says what it stands for and that it is a demo.
"""

from __future__ import annotations

import io
import zlib

from PIL import Image, ImageDraw, ImageFont

_SIZE = (800, 600)


def card(title: str, subtitle: str = "") -> bytes:
    hue = zlib.crc32(title.encode()) & 0xFFFFFF
    background = (hue >> 16 & 0x7F) + 60, (hue >> 8 & 0x7F) + 60, (hue & 0x7F) + 60
    image = Image.new("RGB", _SIZE, background)
    draw = ImageDraw.Draw(image)
    big, small = _font(56), _font(30)
    draw.text((400, 260), title, font=big, fill="white", anchor="mm")
    if subtitle:
        draw.text((400, 340), subtitle, font=small, fill="white", anchor="mm")
    draw.text((400, 560), "KORA — DÉMO", font=small, fill=(255, 255, 255), anchor="mm")
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=80)
    return out.getvalue()


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("DejaVuSans-Bold.ttf", "Arial Bold.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)
