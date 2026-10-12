"""Who a review or a complaint is about, and what opened the right to file one.

Two decisions from the spec are encoded here rather than left to callers.

First, **the sanction targets the provider, not the offer** (§3). A landlord
banned on one listing republishes the same lodging under a new one and nothing
has been settled, so every record carries a :class:`ProviderRef` and keeps the
offer only as the thing that was judged.

Second, **standing follows the introduction, not the payment** (§7). Payment
does not even designate the same person across domains — in Food the
*restaurant* pays the subscription and the *client* complains, and in Dating the
person who was unlocked paid nothing yet is the one who must be able to report
harassment. So the thing that opens the right to file is an
:class:`IntroductionRef`: the moment Kora put the two people in touch.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from loka.shared.domain.value_object import ValueObject


class ServiceDomain(StrEnum):
    """The four businesses this context is shared by."""

    HOUSING = "HOUSING"
    FOOD = "FOOD"
    DATING = "DATING"
    FOUND = "FOUND"


class ProviderKind(StrEnum):
    """The person who offers the service, named per domain (§3)."""

    LANDLORD = "LANDLORD"
    RESTAURATEUR = "RESTAURATEUR"
    FINDER = "FINDER"
    DATING_PROFILE = "DATING_PROFILE"


_DOMAIN_PROVIDER: dict[ServiceDomain, ProviderKind] = {
    ServiceDomain.HOUSING: ProviderKind.LANDLORD,
    ServiceDomain.FOOD: ProviderKind.RESTAURATEUR,
    ServiceDomain.DATING: ProviderKind.DATING_PROFILE,
    ServiceDomain.FOUND: ProviderKind.FINDER,
}


def provider_kind_for(domain: ServiceDomain) -> ProviderKind:
    """The only provider kind a given domain can produce.

    Checked rather than trusted: a Food complaint filed against a LANDLORD
    would count towards the wrong person's hide threshold, and nothing later in
    the chain would notice.
    """
    return _DOMAIN_PROVIDER[domain]


@dataclass(frozen=True, slots=True)
class ProviderRef(ValueObject):
    """The sanctionable party: a kind plus its id in the owning domain.

    The kind is part of the identity because provider ids come from four
    different tables, and two of them could collide in principle.
    """

    kind: ProviderKind
    provider_id: uuid.UUID

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, uuid.UUID):
            self._reject("provider_id must be a UUID", provider_id=self.provider_id)

    def belongs_to(self, domain: ServiceDomain) -> bool:
        return self.kind is provider_kind_for(domain)

    def to_primitive(self) -> dict[str, object]:
        return {"kind": self.kind.value, "provider_id": str(self.provider_id)}


@dataclass(frozen=True, slots=True)
class OfferRef(ValueObject):
    """What was judged: a listing, a menu, a profile, a found document.

    A review bears on an offer *and* its provider (§2); only the provider is
    ever sanctioned. The offer is carried so the ranking can tell which of one
    landlord's three lodgings earned the good reviews.
    """

    domain: ServiceDomain
    offer_id: uuid.UUID

    def __post_init__(self) -> None:
        if not isinstance(self.offer_id, uuid.UUID):
            self._reject("offer_id must be a UUID", offer_id=self.offer_id)

    def to_primitive(self) -> dict[str, object]:
        return {"domain": self.domain.value, "offer_id": str(self.offer_id)}


@dataclass(frozen=True, slots=True)
class IntroductionRef(ValueObject):
    """The introduction that gives one user standing to speak about one offer.

    Opaque on purpose: what counts as an introduction differs per domain — a
    settled unlock in Housing and Found, a contact unlock on either side in
    Dating, a reservation request in Food — and this context must not know how
    any of them is stored. The owning domain answers whether this reference is
    real, through :class:`IntroductionDirectory`.

    It is also the uniqueness key for both a review and a complaint: one
    introduction, one voice. Without it a single angry tenant could file the
    whole hide threshold on his own, and a landlord could rate his own listing
    as many times as he liked.
    """

    domain: ServiceDomain
    introduction_id: uuid.UUID

    def __post_init__(self) -> None:
        if not isinstance(self.introduction_id, uuid.UUID):
            self._reject("introduction_id must be a UUID", introduction_id=self.introduction_id)

    def to_primitive(self) -> dict[str, object]:
        return {"domain": self.domain.value, "introduction_id": str(self.introduction_id)}
