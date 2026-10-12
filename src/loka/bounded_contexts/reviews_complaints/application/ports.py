"""Ports this context depends on.

Only one, and it points at the four business domains: Kora has to know whether
she really introduced this person to this provider before accepting a word
about him. Declared here because the rule — standing follows the introduction
(§7) — belongs to this context; *what* an introduction is belongs to Housing,
Food, Dating and Found, and each implements the adapter over its own tables.

That direction is deliberate. Reading the access grants or the reservation
requests from here would couple Reviews & Complaints to four schemas it has no
business knowing, and would break the first time one of them changed.
"""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from loka.bounded_contexts.reviews_complaints.domain.value_objects.provider import (
    IntroductionRef,
    OfferRef,
    ProviderRef,
)


@runtime_checkable
class IntroductionDirectory(Protocol):
    """Did Kora put this user in touch with this provider, over this offer?

    One boolean, which is the whole of what this context is allowed to learn
    about a domain's records. It must answer False when it cannot tell: an
    unknown introduction accepted as real is a fake review, or a complaint that
    counts towards hiding an innocent provider.

    Both sides of a Dating unlock answer True. The person who was unlocked paid
    nothing and is precisely the one who must be able to report harassment, so
    an implementation that checks a payment instead of an introduction is wrong
    even where a payment exists.
    """

    async def introduced(
        self,
        *,
        introduction: IntroductionRef,
        user_id: uuid.UUID,
        provider: ProviderRef,
        offer: OfferRef,
    ) -> bool: ...
