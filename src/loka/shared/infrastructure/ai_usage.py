"""What a model call outside the main pipeline cost, written to the ledger.

The photo, menu, match and talk readers were never counted: the daily and
per-user budgets saw a fraction of the spend (decision of 25 Nov 2026).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

# (use_case, model, input_tokens, output_tokens, latency_ms)
RecordUsage = Callable[[str, str, int, int, int], Awaitable[None]]


def tokens_of(payload: dict[str, Any]) -> tuple[int, int]:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return 0, 0
    return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
