"""The bus plan sent to a traveller: every seat numbered, the taken ones greyed.

Drawn from the line's own seat count and layout ("2+2", "2+1"), driver at the
front, so the number the traveller replies with is the one printed on the seat.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

from loka.bounded_contexts.booking.domain.line import LAYOUTS

_SEAT, _GAP, _AISLE, _MARGIN, _HEADER = 70, 10, 50, 30, 120
_FREE, _TAKEN, _INK, _BG = (46, 139, 87), (190, 190, 190), (30, 30, 30), (255, 255, 255)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("DejaVuSans-Bold.ttf", "Arial Bold.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def seat_map_png(*, title: str, subtitle: str, seats: int, layout: str, taken: set[int]) -> bytes:
    left, right = LAYOUTS[layout]
    per_row = left + right
    rows = -(-seats // per_row)
    width = _MARGIN * 2 + per_row * (_SEAT + _GAP) + _AISLE
    height = _HEADER + _MARGIN * 2 + (rows + 1) * (_SEAT + _GAP) + 40
    image = Image.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(image)
    draw.text((_MARGIN, 18), title, fill=_INK, font=_font(26))
    draw.text((_MARGIN, 56), subtitle, fill=_INK, font=_font(20))
    draw.text(
        (_MARGIN, 86), "Vert = libre   Gris = occupé", fill=(90, 90, 90), font=_font(18)
    )
    top = _HEADER + _MARGIN
    draw.rounded_rectangle(
        (width - _MARGIN - _SEAT, top, width - _MARGIN, top + _SEAT), 12, outline=_INK, width=3
    )
    draw.text((width - _MARGIN - _SEAT + 6, top + 22), "Chauf.", fill=_INK, font=_font(16))
    number, font = 1, _font(24)
    for row in range(rows):
        y = top + (row + 1) * (_SEAT + _GAP)
        for column in range(per_row):
            if number > seats:
                break
            x = _MARGIN + column * (_SEAT + _GAP) + (_AISLE if column >= left else 0)
            colour = _TAKEN if number in taken else _FREE
            draw.rounded_rectangle((x, y, x + _SEAT, y + _SEAT), 12, fill=colour)
            label = str(number)
            box = draw.textbbox((0, 0), label, font=font)
            draw.text(
                (x + (_SEAT - (box[2] - box[0])) / 2, y + (_SEAT - (box[3] - box[1])) / 2 - 4),
                label,
                fill=_BG,
                font=font,
            )
            number += 1
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
