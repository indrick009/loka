"""Property context exceptions."""

from __future__ import annotations

from loka.shared.domain.errors import DomainError


class PublicationBlocked(DomainError):
    """A business rule prevents publication; the listing stays a draft."""

    code = "publication_blocked"


class PropertyNotEditable(DomainError):
    code = "property_not_editable"


class DuplicateListing(DomainError):
    code = "duplicate_listing"