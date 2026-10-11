"""Property aggregate.

The property is the consistency boundary for a listing. It owns its
availability truth: a property is never considered available unless the
aggregate says so, which is what keeps a cached RENTED property from being
served as bookable.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from loka.bounded_contexts.property.domain.entities.property_media import (
    MediaKind,
    MediaStatus,
    PropertyMedia,
)
from loka.bounded_contexts.property.domain.exceptions.property_errors import (
    PublicationBlocked,
)
from loka.bounded_contexts.property.domain.value_objects.enums import (
    AvailabilityAnswer,
    AvailabilityWindow,
    BedroomCount,
    ChargingPolicy,
    DataQualityIssue,
    Duration,
    PropertyDraftData,
    PropertyStanding,
    PropertyStatus,
    PropertyType,
    RentMonths,
    SurfaceArea,
)
from loka.bounded_contexts.property.domain.value_objects.location import Location
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.shared.domain.aggregate import AggregateRoot
from loka.shared.domain.clock import ensure_utc
from loka.shared.domain.errors import InvalidStateTransition

_ALLOWED_TRANSITIONS: dict[PropertyStatus, frozenset[PropertyStatus]] = {
    PropertyStatus.DRAFT: frozenset({PropertyStatus.AVAILABLE}),
    PropertyStatus.AVAILABLE: frozenset(
        {
            PropertyStatus.RESERVED,
            PropertyStatus.RENTED,
            PropertyStatus.UNAVAILABLE,
            PropertyStatus.SUSPENDED,
            PropertyStatus.DRAFT,
            PropertyStatus.AVAILABLE,
        }
    ),
    PropertyStatus.RESERVED: frozenset(
        {
            PropertyStatus.AVAILABLE,
            PropertyStatus.RENTED,
            PropertyStatus.UNAVAILABLE,
            PropertyStatus.SUSPENDED,
        }
    ),
    PropertyStatus.UNAVAILABLE: frozenset(
        {
            PropertyStatus.AVAILABLE,
            PropertyStatus.RENTED,
            PropertyStatus.SUSPENDED,
            PropertyStatus.DRAFT,
        }
    ),
    PropertyStatus.RENTED: frozenset(
        {
            PropertyStatus.AVAILABLE,
            PropertyStatus.UNAVAILABLE,
            PropertyStatus.ARCHIVED,
            PropertyStatus.SUSPENDED,
        }
    ),
    PropertyStatus.SUSPENDED: frozenset(
        {PropertyStatus.AVAILABLE, PropertyStatus.UNAVAILABLE, PropertyStatus.ARCHIVED}
    ),
    PropertyStatus.ARCHIVED: frozenset(),
}


@dataclass(slots=True)
class Amenities:
    """Amenities of a listing.

    ``None`` means "unknown", which is distinct from ``False`` ("absent"): the
    difference drives the data-quality report sent back to the landlord.
    """

    parking: bool | None = None
    water: bool | None = None
    electricity: bool | None = None
    internet: bool | None = None
    security: bool | None = None
    extras: frozenset[str] = frozenset()

    def missing_flags(self) -> frozenset[str]:
        return frozenset(
            name
            for name in ("parking", "water", "electricity", "internet", "security")
            if getattr(self, name) is None
        )


class Property(AggregateRoot):
    __slots__ = (
        "advance_months",
        "amenities",
        "availability",
        "bedrooms",
        "charges",
        "charging_policy",
        "conditions",
        "created_at",
        "data_quality_issues",
        "deposit_months",
        "description",
        "is_verified",
        "landlord_id",
        "landlord_rules",
        "last_availability_confirmed_at",
        "location",
        "media",
        "minimum_duration",
        "pending_draft_steps",
        "property_id",
        "property_type",
        "published_at",
        "quantity",
        "rent",
        "standing",
        "status",
        "surface_area",
        "updated_at",
    )

    def __init__(
        self,
        *,
        property_id: uuid.UUID,
        landlord_id: uuid.UUID,
        now: datetime,
    ) -> None:
        super().__init__(aggregate_type="Property")
        self._assign_id(property_id)
        self.property_id = property_id
        self.landlord_id = landlord_id
        self.status = PropertyStatus.DRAFT
        self.property_type: PropertyType | None = None
        self.standing: PropertyStanding | None = None
        self.location: Location | None = None
        self.rent: Money | None = None
        self.charges: Money | None = None
        self.charging_policy = ChargingPolicy.UNKNOWN
        # Both counted in months of rent, because that is how this market
        # counts entry money: "je paye 6 mois de loyer et j'ajoute deux mois
        # comme caution remboursable". Francs would decouple the day the
        # landlord corrects his rent, and the tenant's "j'ai 4 mois" would need
        # converting before it could be compared with what is being asked.
        self.deposit_months: RentMonths | None = None
        self.advance_months: RentMonths | None = None
        self.bedrooms: BedroomCount | None = None
        self.surface_area: SurfaceArea | None = None
        self.amenities = Amenities()
        self.minimum_duration: Duration | None = None
        self.availability: AvailabilityWindow | None = None
        self.conditions: str | None = None
        self.description: str | None = None
        self.media: list[PropertyMedia] = []
        # One listing can stand for several identical lodgings (§5). It is what
        # makes "is it still available?" mean "are any left?".
        #
        # ``None`` means the landlord has not been asked yet, which is not the
        # same as one lodging. Without the distinction, step 3 of the form is
        # the single step that could never be reported as missing, so a
        # landlord who skipped it would never be prompted again.
        self.quantity: int | None = None
        self.landlord_rules: str | None = None
        self.last_availability_confirmed_at: datetime | None = None
        self.data_quality_issues: set[DataQualityIssue] = set()
        self.published_at: datetime | None = None
        self.pending_draft_steps = set(PropertyDraftData)
        self.is_verified = False
        self.created_at = ensure_utc(now)
        self.updated_at = ensure_utc(now)

    # -- guarded transitions ------------------------------------------------

    def _assert_transition(self, target: PropertyStatus, *, action: str) -> None:
        allowed = _ALLOWED_TRANSITIONS[self.status]
        if target not in allowed:
            raise InvalidStateTransition(
                f"Property: cannot {action} from {self.status.value} to {target.value}",
                context={
                    "property_id": str(self.id),
                    "from": self.status.value,
                    "to": target.value,
                },
            )

    def _touch(self, now: datetime) -> None:
        self.updated_at = ensure_utc(now)
        self._bump()

    # -- draft edits --------------------------------------------------------

    def set_type(self, property_type: PropertyType, *, now: datetime) -> None:
        self._assert_editable()
        self.property_type = property_type
        self.pending_draft_steps.discard(PropertyDraftData.TYPE)
        self._touch(now)

    def set_standing(self, standing: PropertyStanding, *, now: datetime) -> None:
        self._assert_editable()
        self.standing = standing
        self._touch(now)

    def set_location(self, location: Location, *, now: datetime) -> None:
        self._assert_editable()
        if not location.is_publishable():
            self._reject(
                "location must be precise enough to publish",
                precision=location.precision.value,
            )
        self.location = location
        self.pending_draft_steps.discard(PropertyDraftData.LOCATION)
        self.data_quality_issues.discard(DataQualityIssue.VAGUE_LOCATION)
        self._touch(now)

    def set_rent(self, rent: Money, *, now: datetime) -> None:
        self._assert_editable()
        self.rent = rent.require_positive(reason="rent must be strictly positive")
        self.pending_draft_steps.discard(PropertyDraftData.PRICE)
        self._touch(now)

    def set_charges(self, charges: Money, policy: ChargingPolicy, *, now: datetime) -> None:
        self._assert_editable()
        self.charges = charges.require_non_negative(reason="charges cannot be negative")
        self.charging_policy = policy
        self.pending_draft_steps.discard(PropertyDraftData.CHARGES)
        self.data_quality_issues.discard(DataQualityIssue.MISSING_CHARGES)
        self._touch(now)

    def set_deposit_months(self, deposit: RentMonths, *, now: datetime) -> None:
        """The refundable caution, in months of rent.

        Zero is a real answer — "aucune caution" is ordinary here — which is why
        it is recorded rather than left unset. The landlord fixes it; Kora
        stores it, shows it, and neither holds the money nor arbitrates its
        return (see conception_metier/kora-housing-v1.md §4.7).
        """
        self._assert_editable()
        self.deposit_months = deposit
        self.pending_draft_steps.discard(PropertyDraftData.DEPOSIT)
        self.data_quality_issues.discard(DataQualityIssue.MISSING_DEPOSIT)
        self._touch(now)

    def set_advance_months(self, advance: RentMonths, *, now: datetime) -> None:
        """How many months of rent the landlord wants up front.

        On top of the caution, and spent rather than returned. This is the
        figure the matching compares against what a tenant says he can pay.
        """
        self._assert_editable()
        self.advance_months = advance
        self.data_quality_issues.discard(DataQualityIssue.MISSING_ADVANCE)
        self._touch(now)

    @property
    def entry_months(self) -> int | None:
        """Months a tenant must hand over to move in: advance plus caution.

        ``None`` while either is unknown: a partial total read as a total is
        worse than no total, because a tenant would budget against it.
        """
        if self.deposit_months is None or self.advance_months is None:
            return None
        return self.advance_months.months + self.deposit_months.months

    def entry_cost(self) -> Money | None:
        """What moving in costs in francs, at this listing's rent."""
        months = self.entry_months
        if months is None or self.rent is None:
            return None
        return Money(amount=self.rent.amount * months, currency=self.rent.currency)

    def set_quantity(self, quantity: int, *, now: datetime) -> None:
        """How many identical lodgings this one listing stands for.

        Same type, same characteristics, same price: one listing, quantity two.
        Different prices make two listings, because the tenant is comparing a
        price (§5).
        """
        self._assert_editable()
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
            self._reject("a listing must stand for at least one lodging", quantity=quantity)
        self.quantity = quantity
        self.data_quality_issues.discard(DataQualityIssue.MISSING_QUANTITY)
        self._touch(now)

    @property
    def lodgings(self) -> int:
        """How many are on offer, reading an unanswered step as one.

        Everything downstream — "are any left?", search, the decrement — needs a
        number, and one lodging is the only safe reading of a listing nobody
        said was plural.
        """
        return 1 if self.quantity is None else self.quantity

    @property
    def is_exhausted(self) -> bool:
        return self.lodgings == 0

    def declare_one_taken(self, *, now: datetime) -> None:
        """The landlord says one of them is gone.

        Only he can say it. Kora leaves the relationship at the introduction, so
        it never learns whether a lease was signed — which is why nothing here
        decrements on a match, a confirmation or a payment (§9).

        Deliberately usable on a published listing: this is what a landlord
        answers to "le logement est encore disponible ?", long after publication.
        """
        if self.is_exhausted:
            self._reject("every lodging of this listing is already taken")
        self.quantity = self.lodgings - 1
        self._touch(now)

    def set_rooms(
        self, bedrooms: BedroomCount, surface: SurfaceArea | None, *, now: datetime
    ) -> None:
        self._assert_editable()
        self.bedrooms = bedrooms
        self.surface_area = surface
        self.pending_draft_steps.discard(PropertyDraftData.FEATURES)
        self._touch(now)

    def update_amenities(self, amenities: Amenities, *, now: datetime) -> None:
        self._assert_editable()
        self.amenities = amenities
        self.pending_draft_steps.discard(PropertyDraftData.FEATURES)
        self._touch(now)

    def set_minimum_duration(self, duration: Duration, *, now: datetime) -> None:
        self._assert_editable()
        self.minimum_duration = duration
        self.pending_draft_steps.discard(PropertyDraftData.MINIMUM_DURATION)
        self.data_quality_issues.discard(DataQualityIssue.MISSING_MINIMUM_DURATION)
        self._touch(now)

    def set_availability(self, window: AvailabilityWindow, *, now: datetime) -> None:
        self._assert_editable()
        self.availability = window
        self.pending_draft_steps.discard(PropertyDraftData.AVAILABILITY)
        self.data_quality_issues.discard(DataQualityIssue.MISSING_AVAILABILITY_DATE)
        self._touch(now)

    def set_conditions(self, conditions: str, *, now: datetime) -> None:
        self._assert_editable()
        cleaned = conditions.strip()
        if len(cleaned) < 10:
            self._reject("conditions are too vague to publish", conditions=conditions)
        self.conditions = cleaned
        self.pending_draft_steps.discard(PropertyDraftData.CONDITIONS)
        self.data_quality_issues.discard(DataQualityIssue.AMBIGUOUS_CONDITIONS)
        self._touch(now)

    def set_landlord_rules(self, rules: str, *, now: datetime) -> None:
        self._assert_editable()
        self.landlord_rules = rules.strip() or None
        self._touch(now)

    def add_media(self, media: PropertyMedia, *, now: datetime) -> None:
        self._assert_editable()
        if media.kind is not MediaKind.DOCUMENT and len(self.media) >= 30:
            self._reject("a listing cannot exceed 30 photos or videos")
        if any(existing.object_key == media.object_key for existing in self.media):
            self._reject("media is already attached", object_key=media.object_key)
        media.position = len(self.media)
        self.media.append(media)
        self.pending_draft_steps.discard(PropertyDraftData.MEDIA)
        self.data_quality_issues.discard(DataQualityIssue.MISSING_PHOTOS)
        self._touch(now)

    def remove_media(self, media_id: uuid.UUID, *, now: datetime) -> None:
        self.media = [m for m in self.media if m.media_id != media_id]
        for position, item in enumerate(self.media):
            item.position = position
        self._touch(now)

    def _assert_editable(self) -> None:
        if self.status in (PropertyStatus.RENTED, PropertyStatus.ARCHIVED):
            self._reject(
                "content cannot be edited while rented or archived",
                status=self.status.value,
            )
        if self.status is PropertyStatus.SUSPENDED:
            self._reject("content cannot be edited while suspended", status=self.status.value)

    # -- quality gate -------------------------------------------------------

    def blocking_issues(self) -> frozenset[DataQualityIssue]:
        issues: set[DataQualityIssue] = set()
        if not self.usable_photos():
            issues.add(DataQualityIssue.MISSING_PHOTOS)
        if self.rent is None or self.charging_policy is ChargingPolicy.UNKNOWN:
            issues.add(DataQualityIssue.MISSING_CHARGES)
        # The minimum duration, the availability date and the entry conditions
        # are deliberately *not* blocking. Kora's ten collection steps never ask
        # for them: it puts a landlord and a tenant in touch, it does not
        # negotiate a lease — see conception_metier/kora-housing-v1.md §4 and
        # §10. Requiring them meant a landlord could answer every question he
        # was actually asked and still be refused publication, for three fields
        # nobody had mentioned to him.
        #
        # The fields themselves remain, and their setters still clear the
        # matching issue, so a landlord who volunteers "disponible
        # immédiatement" has it recorded. Optional, not forbidden.
        if self.location is None or not self.location.is_publishable():
            issues.add(DataQualityIssue.VAGUE_LOCATION)
        if self.deposit_months is None:
            issues.add(DataQualityIssue.MISSING_DEPOSIT)
        if self.advance_months is None:
            # Blocking, unlike the three fields above, because the form really
            # does ask for it (step 8) and because a listing with no advance
            # cannot be matched: the tenant's months have nothing to meet.
            issues.add(DataQualityIssue.MISSING_ADVANCE)
        return frozenset(issues)

    def usable_photos(self) -> list[PropertyMedia]:
        """Photos that are uploaded and not quarantined.

        A quarantined photo (suspected stock image, duplicate of another
        listing) does not count towards publication readiness.
        """
        return [
            media
            for media in self.media
            if media.kind is MediaKind.PHOTO and media.status is MediaStatus.UPLOADED
        ]

    def completeness_score(self) -> float:
        """How much of the form the landlord actually answered.

        One check per step of the ten the landlord is asked (§4), and nothing
        else. It used to count the minimum duration, the availability date and
        the free-text conditions — three questions the form no longer asks — so
        a listing conforming perfectly to the spec scored 7 out of 10 and was
        reported back to its owner as incomplete.
        """
        total = 10
        checks = (
            self.property_type is not None,
            self.standing is not None,
            self.quantity is not None,
            self.location is not None and self.location.is_publishable(),
            self.rent is not None,
            self.charging_policy is not ChargingPolicy.UNKNOWN,
            self.deposit_months is not None,
            self.advance_months is not None,
            self.amenities.security is not None or self.amenities.parking is not None,
            bool(self.usable_photos()),
        )
        return sum(checks) / total

    def draft_summary(self) -> dict[str, object]:
        return {
            "property_id": str(self.id),
            "type": self.property_type.value if self.property_type else None,
            "location": self.location.city if self.location else None,
            "neighbourhood": self.location.neighbourhood if self.location else None,
            "rent": self.rent.amount if self.rent else None,
            "charges": self.charges.amount if self.charges else None,
            "charging_policy": self.charging_policy.value,
            "bedrooms": self.bedrooms.count if self.bedrooms else None,
            "bathrooms": self.bedrooms.bathrooms if self.bedrooms else None,
            "surface": self.surface_area.square_metres if self.surface_area else None,
            "minimum_duration": self.minimum_duration.label if self.minimum_duration else None,
            "availability": (
                self.availability.available_from.isoformat() if self.availability else None
            ),
            "photos": len(self.media),
            "conditions": self.conditions,
            "missing": sorted(issue.value for issue in self.blocking_issues()),
        }

    # -- lifecycle ----------------------------------------------------------

    def publish(self, *, now: datetime, actor_id: uuid.UUID | None = None) -> None:
        """Publish only when the listing is complete and the landlord may publish."""
        if self.status is not PropertyStatus.DRAFT and self.status is not PropertyStatus.AVAILABLE:
            raise InvalidStateTransition(
                "Property: only a draft or available property can be published",
                context={"status": self.status.value},
            )
        issues = self.blocking_issues()
        if issues:
            raise PublicationBlocked(
                "listing is incomplete",
                context={"missing": sorted(issue.value for issue in issues)},
            )
        self.status = PropertyStatus.AVAILABLE
        self.published_at = self.published_at or ensure_utc(now)
        self.pending_draft_steps.clear()
        self.data_quality_issues = set(issues)
        self.last_availability_confirmed_at = ensure_utc(now)
        self._touch(now)
        self.record(
            "PropertyPublished",
            occurred_at=now,
            aggregate_id=self.id,
            actor_id=actor_id,
            payload={"property_id": str(self.id), "landlord_id": str(self.landlord_id)},
        )

    def mark_verified(self, *, now: datetime) -> None:
        self.is_verified = True
        self._touch(now)

    def reserve(self, *, now: datetime, actor_id: uuid.UUID | None = None) -> None:
        self._assert_transition(PropertyStatus.RESERVED, action="reserve")
        self.status = PropertyStatus.RESERVED
        self._touch(now)
        self.record(
            "PropertyReserved",
            occurred_at=now,
            actor_id=actor_id,
            payload={"property_id": str(self.id)},
        )

    def release_reservation(self, *, now: datetime, reason: str | None = None) -> None:
        if self.status is not PropertyStatus.RESERVED:
            self._reject("only a reserved property can be released", status=self.status.value)
        self.status = PropertyStatus.AVAILABLE
        self._touch(now)
        self.record(
            "PropertyReservationReleased",
            occurred_at=now,
            payload={"property_id": str(self.id), "reason": reason},
        )

    def mark_as_rented(
        self, *, now: datetime, actor_id: uuid.UUID | None = None
    ) -> None:
        self._assert_transition(PropertyStatus.RENTED, action="mark rented")
        self.status = PropertyStatus.RENTED
        self._touch(now)
        self.record(
            "PropertyRented",
            occurred_at=now,
            actor_id=actor_id,
            payload={"property_id": str(self.id), "landlord_id": str(self.landlord_id)},
        )

    def mark_unavailable(self, *, now: datetime, reason: str | None = None) -> None:
        self._assert_transition(PropertyStatus.UNAVAILABLE, action="mark unavailable")
        self.status = PropertyStatus.UNAVAILABLE
        self._touch(now)
        self.record(
            "PropertyMarkedUnavailable",
            occurred_at=now,
            payload={"property_id": str(self.id), "reason": reason},
        )

    def relist(self, *, now: datetime, window: AvailabilityWindow) -> None:
        if self.status not in (PropertyStatus.RENTED, PropertyStatus.UNAVAILABLE):
            self._reject(
                "only a rented or unavailable property can be relisted",
                status=self.status.value,
            )
        self.availability = window
        self.status = PropertyStatus.AVAILABLE
        self.last_availability_confirmed_at = ensure_utc(now)
        self._touch(now)
        self.record(
            "PropertyPublished",
            occurred_at=now,
            payload={"property_id": str(self.id), "relisted": True},
        )

    def suspend(self, *, now: datetime, reason: str) -> None:
        self.status = PropertyStatus.SUSPENDED
        self._touch(now)
        self.record(
            "PropertySuspended",
            occurred_at=now,
            payload={"property_id": str(self.id), "reason": reason},
        )

    def archive(self, *, now: datetime) -> None:
        if self.status is PropertyStatus.RENTED:
            self._reject("a rented property must be marked unavailable first")
        self.status = PropertyStatus.ARCHIVED
        self._touch(now)
        self.record("PropertyArchived", occurred_at=now, payload={"property_id": str(self.id)})

    # -- availability re-confirmation --------------------------------------

    def answer_availability(
        self,
        answer: AvailabilityAnswer,
        *,
        now: datetime,
        window: AvailabilityWindow | None = None,
    ) -> None:
        """Apply the landlord's answer to a periodic availability check."""
        self.last_availability_confirmed_at = ensure_utc(now)
        if answer is AvailabilityAnswer.STILL_AVAILABLE:
            if self.status is not PropertyStatus.AVAILABLE:
                self._reject(
                    "cannot confirm availability for a non-available listing",
                    status=self.status.value,
                )
            self._touch(now)
            self.record(
                "PropertyAvailabilityConfirmed",
                occurred_at=now,
                payload={"property_id": str(self.id), "answer": answer.value},
            )
            return
        if answer is AvailabilityAnswer.RENTED:
            self.mark_as_rented(now=now)
            return
        if answer is AvailabilityAnswer.TEMPORARILY_UNAVAILABLE:
            self.mark_unavailable(now=now, reason="landlord_reported_temporarily_unavailable")
            if window is not None:
                self.availability = window
            return
        self._reject("unknown availability answer", answer=answer.value)

    # -- quality scan -------------------------------------------------------

    def rescan_quality(self, *, market_reference: Money | None = None) -> set[DataQualityIssue]:
        issues = set(self.blocking_issues())
        if (
            market_reference is not None
            and self.rent is not None
            and self.rent < market_reference.percentage_of(0.4)
        ):
            issues.add(DataQualityIssue.SUSPICIOUS_PRICE)
        self.data_quality_issues = issues
        return issues

    def missing_summary(self) -> list[str]:
        return sorted(issue.value for issue in self.blocking_issues())

    @property
    def is_publicly_visible(self) -> bool:
        return self.status is PropertyStatus.AVAILABLE

    @property
    def public_price(self) -> Money | None:
        if self.rent is None:
            return None
        if self.charges and self.charging_policy is ChargingPolicy.EXTRA:
            return self.rent + self.charges
        return self.rent

    def occupancy_start(self) -> date | None:
        if self.availability is None:
            return None
        if self.minimum_duration is None:
            return self.availability.available_from
        return self.minimum_duration.months_from(self.availability.available_from)