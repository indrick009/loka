"""The conversation journal: every message in, every reading, every message out.

One JSON line per event, appended to ``CONVERSATION_LOG_PATH`` (empty: off).
It is a file, not a table, so ``_empty_all_data`` never erases it: it is what
a test conversation is read back from afterwards, to see where idia went wrong.
It holds what users write: kept ``CONVERSATION_LOG_RETENTION_DAYS`` days (30
by default, a line older is dropped once a day by the worker), and erased for
a user who asks (``persistence/erasure``).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
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


def prune(now: datetime | None = None) -> int:
    """Drop the lines older than the retention; the number dropped."""
    settings = get_settings()
    if not settings.conversation_log_path:
        return 0
    target = Path(settings.conversation_log_path)
    cutoff = ((now or datetime.now(UTC)) - timedelta(days=settings.conversation_log_retention_days))
    limit = cutoff.isoformat(timespec="seconds")
    try:
        lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return 0
    # ISO timestamps in UTC compare as text; a line that cannot be read is kept.
    kept = [line for line in lines if not _older(line, limit)]
    if len(kept) == len(lines):
        return 0
    try:
        tmp = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
        tmp.write_text("".join(kept), encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        _logger.warning("conversation_log_prune_failed", error=type(exc).__name__)
        return 0
    return len(lines) - len(kept)


def _older(line: str, limit: str) -> bool:
    try:
        at = json.loads(line).get("at")
    except ValueError:
        return False
    return isinstance(at, str) and at < limit
