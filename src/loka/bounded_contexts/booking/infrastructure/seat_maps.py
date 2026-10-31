"""The bus plan drawn, then stored where the WhatsApp gateway can send it from."""

from __future__ import annotations

import asyncio
from datetime import datetime

from loka.bounded_contexts.booking.domain.line import DOUALA, Line
from loka.bounded_contexts.booking.infrastructure.seat_map import seat_map_png
from loka.shared.infrastructure.logging import get_logger
from loka.shared.infrastructure.object_storage.media import MediaStorage

_logger = get_logger(__name__)


class StoredSeatMaps:
    def __init__(self, storage: MediaStorage) -> None:
        self._storage = storage

    async def draw(
        self,
        *,
        line: Line,
        agency: str,
        departs_at: datetime,
        taken: set[int],
        chosen: set[int] | None = None,
    ) -> str | None:
        when = departs_at.astimezone(DOUALA).strftime("%d/%m à %Hh%M")
        png = await asyncio.to_thread(
            seat_map_png,
            title=f"{agency} · {line.origin} → {line.destination}",
            subtitle=f"Départ le {when} · {line.bus_class.lower()} · {line.seats} places",
            seats=line.seats,
            layout=line.layout,
            taken=taken,
            chosen=chosen or set(),
        )
        try:
            stored = await self._storage.put(
                png, owner_id=line.line_id, prefix="booking", content_type="image/png"
            )
        except Exception as exc:  # the plan is a convenience; the text lists seats too
            _logger.warning("seat_map_not_stored", error=str(exc))
            return None
        return stored.object_key
