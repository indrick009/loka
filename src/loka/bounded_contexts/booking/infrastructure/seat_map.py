"""The bus plan sent to a traveller, drawn to look like the bus he will board.

The body, the windscreen, the driver's wheel on the left (Cameroon drives on
the right), the door at the front right, and every seat numbered as a seat:
green is free, red is taken, blue is the one he just picked. Drawn from the
line's own seat count and layout ("2+2", "2+1"), so the number he replies
with is the one printed on the seat.
"""

from __future__ import annotations

import io
from collections.abc import Collection

from PIL import Image, ImageDraw, ImageFont

from loka.bounded_contexts.booking.domain.line import LAYOUTS

_SEAT, _GAP, _AISLE = 64, 12, 56
_SIDE, _HEADER, _CABIN, _TAIL = 34, 150, 150, 40
_WIDTH_MIN = 620

_PAGE = (255, 255, 255)
_INK = (28, 33, 31)
_MUTED = (96, 105, 101)
_BODY = (236, 240, 243)
_BODY_EDGE = (55, 65, 81)
_GLASS = (191, 219, 254)
_FREE = (34, 139, 84)
_TAKEN = (200, 62, 62)
_CHOSEN = (37, 99, 235)

Font = ImageFont.FreeTypeFont | ImageFont.ImageFont
Colour = tuple[int, int, int]


def _font(size: int) -> Font:
    for name in ("DejaVuSans-Bold.ttf", "Arial Bold.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _centred(
    draw: ImageDraw.ImageDraw,
    box: tuple[float, float, float, float],
    text: str,
    font: Font,
    fill: Colour,
) -> None:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    x = box[0] + (box[2] - box[0] - (right - left)) / 2 - left
    y = box[1] + (box[3] - box[1] - (bottom - top)) / 2 - top
    draw.text((x, y), text, fill=fill, font=font)


def _seat(draw: ImageDraw.ImageDraw, x: float, y: float, number: int, colour: Colour) -> None:
    """A seat seen from above: the cushion, and the backrest behind it."""
    draw.rounded_rectangle((x, y, x + _SEAT, y + _SEAT), 14, fill=colour)
    darker = (max(0, colour[0] - 45), max(0, colour[1] - 45), max(0, colour[2] - 45))
    draw.rounded_rectangle((x + 4, y + _SEAT - 14, x + _SEAT - 4, y + _SEAT - 4), 6, fill=darker)
    _centred(draw, (x, y, x + _SEAT, y + _SEAT - 12), str(number), _font(24), _PAGE)


def _legend(draw: ImageDraw.ImageDraw, x: float, y: float) -> None:
    font = _font(18)
    for label, colour in (("Libre", _FREE), ("Occupée", _TAKEN), ("Ta place", _CHOSEN)):
        draw.rounded_rectangle((x, y, x + 22, y + 22), 5, fill=colour)
        draw.text((x + 30, y), label, fill=_MUTED, font=font)
        x += 30 + draw.textlength(label, font=font) + 26


def seat_map_png(
    *,
    title: str,
    subtitle: str,
    seats: int,
    layout: str,
    taken: Collection[int],
    chosen: Collection[int] = (),
) -> bytes:
    left, right = LAYOUTS[layout]
    per_row = left + right
    rows = -(-seats // per_row)
    seats_width = per_row * (_SEAT + _GAP) - _GAP + _AISLE
    body_width = seats_width + 2 * _SIDE
    width = max(body_width + 80, _WIDTH_MIN)
    body_left = (width - body_width) // 2
    body_top = _HEADER
    body_bottom = body_top + _CABIN + rows * (_SEAT + _GAP) + _TAIL
    height = body_bottom + 70

    image = Image.new("RGB", (width, height), _PAGE)
    draw = ImageDraw.Draw(image)
    draw.text((30, 18), title, fill=_INK, font=_font(24))
    draw.text((30, 54), subtitle, fill=_INK, font=_font(18))
    _legend(draw, 30, 92)

    # The body, rounder at the front, with its windscreen.
    draw.rounded_rectangle(
        (body_left, body_top, body_left + body_width, body_bottom),
        46,
        fill=_BODY,
        outline=_BODY_EDGE,
        width=5,
    )
    draw.rounded_rectangle(
        (body_left + 22, body_top + 14, body_left + body_width - 22, body_top + 40),
        12,
        fill=_GLASS,
        outline=_BODY_EDGE,
        width=2,
    )
    _centred(
        draw,
        (body_left, body_top + 14, body_left + body_width, body_top + 40),
        "AVANT",
        _font(14),
        _BODY_EDGE,
    )
    # The driver on the left with his wheel; the door on the right.
    seats_left = body_left + _SIDE
    cabin_top = body_top + 64
    draw.rounded_rectangle(
        (seats_left, cabin_top, seats_left + _SEAT, cabin_top + _SEAT), 14, fill=(120, 128, 136)
    )
    draw.ellipse(
        (seats_left + 14, cabin_top - 10, seats_left + _SEAT - 14, cabin_top + 14),
        outline=_INK,
        width=4,
    )
    _centred(
        draw,
        (seats_left, cabin_top + 16, seats_left + _SEAT, cabin_top + _SEAT),
        "Chauffeur",
        _font(11),
        _PAGE,
    )
    door_x = body_left + body_width
    draw.rectangle((door_x - 6, cabin_top - 4, door_x + 6, cabin_top + _SEAT + 4), fill=_PAGE)
    draw.text((door_x - 64, cabin_top + _SEAT + 8), "Porte", fill=_MUTED, font=_font(14))

    taken_set, chosen_set = set(taken), set(chosen)
    number = 1
    for row in range(rows):
        y = body_top + _CABIN + row * (_SEAT + _GAP)
        for column in range(per_row):
            if number > seats:
                break
            x = seats_left + column * (_SEAT + _GAP) + (_AISLE if column >= left else 0)
            colour = (
                _CHOSEN if number in chosen_set else _TAKEN if number in taken_set else _FREE
            )
            _seat(draw, x, y, number, colour)
            number += 1

    _centred(
        draw,
        (0, body_bottom + 18, width, body_bottom + 50),
        "Réponds avec le numéro de ta place",
        _font(18),
        _MUTED,
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
