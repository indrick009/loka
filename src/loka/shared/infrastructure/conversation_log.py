"""The conversation journal: every message in, every reading, every message out.

One JSON line per event, appended to ``CONVERSATION_LOG_PATH`` (empty: off).
It is a file, not a table, so ``_empty_all_data`` never erases it: it is what
a test conversation is read back from afterwards, to see where Kora went wrong.
It holds what users write, so it is for the test phase, not for real traffic.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loka.shared.infrastructure.config.settings import get_settings
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)


def journal(event: str, phone: str | None, **fields: Any) -> None:
    """Append one line; a journal that cannot be written never stops a turn."""
    path = get_settings().conversation_log_path
    if not path:
        return
    line = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "event": event,
            "phone": phone, **{k: v for k, v in fields.items() if v not in (None, "", {})}}
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        _logger.warning("conversation_log_failed", error=type(exc).__name__)
