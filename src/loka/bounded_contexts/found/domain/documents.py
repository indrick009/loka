"""A found document, and an owner's claim to it.

Nothing about a found document is ever shown to someone searching: a claim
learns only that a *possible* match exists, then must prove ownership, and a
person from the Kora team decides before anyone is put in touch.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loka.shared.domain.knowledge.market import normalize

DOCUMENT_TYPES = ("CNI", "PASSEPORT", "PERMIS", "CARTE_ETUDIANT", "AUTRE")
MAX_CLAIMS_PER_DAY = 3

SEARCHING = "SEARCHING"
MATCHED = "MATCHED"
PENDING_REVIEW = "PENDING_REVIEW"
VALIDATED = "VALIDATED"
REJECTED = "REJECTED"


@dataclass(slots=True)
class Declaration:
    declaration_id: uuid.UUID
    finder_user_id: uuid.UUID
    document_type: str
    place: dict[str, Any] | None
    found_on: str
    description: str
    photos: list[str]
    created_at: datetime


@dataclass(slots=True)
class Claim:
    claim_id: uuid.UUID
    owner_user_id: uuid.UUID
    document_type: str
    full_name: str
    place: dict[str, Any] | None
    lost_on: str
    description: str
    created_at: datetime
    status: str = SEARCHING
    declaration_id: uuid.UUID | None = None
    proofs: list[dict[str, str]] = field(default_factory=list)

    def matched(self, declaration: Declaration) -> None:
        self.declaration_id = declaration.declaration_id
        self.status = MATCHED

    def add_proof(self, proof: dict[str, str]) -> None:
        """Proof is the owner's to give: Kora never suggests what it should say."""
        self.proofs.append(proof)
        if self.declaration_id is not None:
            self.status = PENDING_REVIEW


def could_be(claim: Claim, declaration: Declaration) -> bool:
    """Same kind of document, the claimed name in the finder's description,
    and the same city when both are known."""
    if claim.document_type != declaration.document_type:
        return False
    names = [word for word in normalize(claim.full_name).split() if len(word) > 1]
    described = f" {normalize(declaration.description)} "
    present = sum(1 for word in names if f" {word} " in described)
    if not names or present < min(2, len(names)):
        return False
    city_a = (claim.place or {}).get("city")
    city_b = (declaration.place or {}).get("city")
    return not (city_a and city_b and city_a != city_b)
