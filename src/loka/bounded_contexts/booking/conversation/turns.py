"""What a Booking turn does: find departures, show the plan, hold seats, ask to pay."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from typing import Any

from loka.bounded_contexts.booking.conversation import texts
from loka.bounded_contexts.booking.conversation.spec import BOOKING
from loka.bounded_contexts.booking.domain.line import Line
from loka.bounded_contexts.booking.domain.ports import SeatMaps, TicketPayments
from loka.bounded_contexts.booking.domain.reservation import CANCELLED, Reservation
from loka.bounded_contexts.booking.domain.store import BookingStore
from loka.bounded_contexts.conversation.application.plugs.forms import (
    ask_field,
    chosen,
    say,
    stored,
)
from loka.bounded_contexts.conversation.application.plugs.turn import PlugDesk, PlugTurn
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
    TurnOutcome,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import FlowStep

TRIPS_KEY, TRIP_KEY, RESERVATION_KEY = "_trips", "_trip", "_reservation"
MAX_TRIPS = 6
_CRITERIA = ("origin", "destination", "travel_date", "passengers")
_NO = ("non", "no", "annule", "annuler", "stop")
_YES = ("oui", "yes", "ok", "d'accord", "dac", "vas-y", "vas y", "go")


class BookingTurns:
    def __init__(self, store: BookingStore, *, payments: TicketPayments, maps: SeatMaps) -> None:
        self._store = store
        self._payments = payments
        self._maps = maps

    async def apply(self, turn: PlugTurn, desk: PlugDesk) -> TurnOutcome:
        if turn.session.user_id is None:
            return say(texts.cancelled(), voiced=False)
        stage = None if turn.fresh else turn.open_field
        new_criteria = any(name in turn.facts for name in _CRITERIA)
        if stage == "trip" and not new_criteria:
            return await self._pick_trip(turn, desk)
        if stage == "seats" and not new_criteria:
            return await self._pick_seats(turn, desk, turn.session.user_id)
        if stage == "names":
            return await self._names(turn, desk)
        if stage == "pay":
            return await self._pay(turn, desk)
        return await self._search(turn, desk)

    async def _settle(
        self, turn: PlugTurn, desk: PlugDesk, *, key: str | None, context: dict[str, Any]
    ) -> None:
        step = FlowStep.COLLECT_CHOICE if key else FlowStep.PUBLISHED
        await desk.settle(
            turn,
            step=step,
            question_key=BOOKING.question_key(key) if key else None,
            context={**stored(turn.known), **context},
        )

    async def _search(self, turn: PlugTurn, desk: PlugDesk) -> TurnOutcome:
        missing = BOOKING.missing(turn.flow, turn.known)
        if missing:
            field = missing[0]
            key = BOOKING.question_key(field.name)
            await desk.settle(
                turn, step=FlowStep.COLLECT_FIELD, question_key=key, context=stored(turn.known)
            )
            return ask_field(turn, BOOKING, field)
        known = turn.known
        origin, destination = str(known["origin"]), str(known["destination"])
        day = date.fromisoformat(str(known["travel_date"]))
        passengers = int(known["passengers"])
        trips: list[dict[str, Any]] = []
        for line in await self._store.lines_between(origin, destination):
            agency = await self._store.agency(line.agency_id)
            for moment in line.departures(day, now=turn.now):
                taken = await self._store.taken_seats(line.line_id, moment, now=turn.now)
                left = line.seats - len(taken)
                if left >= passengers:
                    trips.append(_trip(line, moment, agency.name if agency else "Agence", left))
        trips.sort(key=lambda trip: (trip["departs_at"], trip["price"]))
        trips = trips[:MAX_TRIPS]
        if not trips:
            key = BOOKING.question_key("travel_date")
            context = {**stored(known), "travel_date": None}
            await desk.settle(turn, step=FlowStep.COLLECT_FIELD, question_key=key, context=context)
            return say(
                texts.no_departure(origin, destination, day.strftime("%d/%m")),
                question=key,
                voiced=False,
            )
        await self._settle(turn, desk, key="trip", context={TRIPS_KEY: trips})
        return say(
            texts.departures(trips, origin, destination),
            question=BOOKING.question_key("trip"),
            voiced=False,
        )

    async def _pick_trip(self, turn: PlugTurn, desk: PlugDesk) -> TurnOutcome:
        raw_trips = turn.session.context.get(TRIPS_KEY)
        trips: list[dict[str, Any]] = raw_trips if isinstance(raw_trips, list) else []
        pick = chosen(turn)
        if pick is None or not 1 <= pick <= len(trips):
            return say(texts.pick_a_number(len(trips)), voiced=False)
        trip = trips[pick - 1]
        line = await self._store.line(uuid.UUID(trip["line_id"]))
        if line is None:
            return await self._search(turn, desk)
        departs_at = datetime.fromisoformat(trip["departs_at"])
        taken = await self._store.taken_seats(line.line_id, departs_at, now=turn.now)
        image = await self._maps.draw(
            line=line, agency=trip["agency"], departs_at=departs_at, taken=taken
        )
        await self._settle(turn, desk, key="seats", context={TRIP_KEY: trip})
        passengers = int(turn.known.get("passengers") or 1)
        text = texts.plan(passengers)
        if image is None:
            free = [seat for seat in range(1, line.seats + 1) if seat not in taken]
            text = f"{text}\n{texts.plan_as_text(free)}"
        outcome = say(text, question=BOOKING.question_key("seats"), voiced=False)
        return TurnOutcome(
            question=outcome.question,
            next_step=outcome.next_step,
            reply_text=outcome.reply_text,
            platform_text=None,
            media_object_key=image,
        )

    async def _pick_seats(self, turn: PlugTurn, desk: PlugDesk, client: uuid.UUID) -> TurnOutcome:
        trip = _trip_of(turn)
        passengers = int(turn.known.get("passengers") or 1)
        seats = list(turn.facts.get("seats") or _numbers(turn.text))
        line = await self._store.line(uuid.UUID(trip["line_id"])) if trip else None
        if line is None:
            return await self._search(turn, desk)
        departs_at = datetime.fromisoformat(trip["departs_at"])
        taken = await self._store.taken_seats(line.line_id, departs_at, now=turn.now)
        clash = sorted(set(seats) & taken)
        valid = (
            len(set(seats)) == passengers
            and not clash
            and all(1 <= seat <= line.seats for seat in seats)
        )
        if not valid:
            return say(texts.wrong_seats(passengers, clash), voiced=False)
        agency = await self._store.agency(line.agency_id)
        reservation = Reservation.hold(
            line_id=line.line_id,
            agency_id=line.agency_id,
            client_user_id=client,
            departs_at=departs_at,
            seats=seats,
            price_xaf=line.price_xaf,
            commission_xaf=agency.commission_xaf if agency else 0,
            now=turn.now,
        )
        await self._store.save(reservation)
        await self._settle(
            turn, desk, key="names", context={RESERVATION_KEY: str(reservation.reservation_id)}
        )
        # The plan again, his seat in blue: he sees what he holds.
        image = await self._maps.draw(
            line=line,
            agency=str(trip.get("agency") or ""),
            departs_at=departs_at,
            taken=taken,
            chosen=set(seats),
        )
        return TurnOutcome(
            question=BOOKING.question_key("names"),
            next_step=FlowStep.COLLECT_CHOICE,
            reply_text=texts.names(passengers),
            platform_text=None,
            media_object_key=image,
        )

    async def _reservation(self, turn: PlugTurn) -> Reservation | None:
        raw = turn.session.context.get(RESERVATION_KEY)
        return await self._store.reservation(uuid.UUID(str(raw))) if raw else None

    async def _names(self, turn: PlugTurn, desk: PlugDesk) -> TurnOutcome:
        reservation = await self._reservation(turn)
        if reservation is None or not reservation.is_live(turn.now):
            return await self._search(turn, desk)
        given = list(turn.facts.get("passenger_names") or _split_names(turn.text))
        if len(given) != len(reservation.seats):
            return say(texts.names(len(reservation.seats)), voiced=False)
        reservation.passengers = given
        await self._store.save(reservation)
        await self._settle(turn, desk, key="pay", context={})
        phone = turn.session.phone.e164
        key = BOOKING.question_key("pay")
        return say(texts.pay(reservation, phone), question=key, voiced=False)

    async def _pay(self, turn: PlugTurn, desk: PlugDesk) -> TurnOutcome:
        reservation = await self._reservation(turn)
        if reservation is None or not reservation.is_live(turn.now):
            return await self._search(turn, desk)
        folded = (turn.text or "").strip().casefold()
        if turn.intent == "NEGATIVE" or folded.startswith(_NO):
            reservation.status = CANCELLED
            await self._store.save(reservation)
            await self._settle(turn, desk, key=None, context={})
            return say(texts.cancelled())
        digits = re.sub(r"\D", "", turn.text or "")
        number = turn.facts.get("payer_phone") or (digits if len(digits) >= 9 else None)
        if number is None and not (turn.intent == "AFFIRMATIVE" or folded.startswith(_YES)):
            return say(texts.pay(reservation, turn.session.phone.e164), voiced=False)
        payer = f"+237{number[-9:]}" if number else turn.session.phone.e164
        trip = _trip_of(turn)
        reference = await self._payments.request(
            reservation_id=reservation.reservation_id,
            amount_xaf=reservation.amount_xaf,
            payer_phone=payer,
            label=f"Kora - billet {trip.get('agency', '')} {reservation.reference}",
        )
        if reference is None:
            return say(texts.payment_refused(), voiced=False)
        reservation.awaiting_payment(payer_phone=payer, payment_reference=reference, now=turn.now)
        await self._store.save(reservation)
        await self._settle(turn, desk, key=None, context={})
        return say(texts.payment_sent(reservation.amount_xaf, payer), voiced=False)


def _trip(line: Line, moment: datetime, agency: str, left: int) -> dict[str, Any]:
    return {
        "line_id": str(line.line_id),
        "departs_at": moment.isoformat(),
        "time": texts.when(moment),
        "agency": agency,
        "price": line.price_xaf,
        "class": line.bus_class,
        "left": left,
    }


def _trip_of(turn: PlugTurn) -> dict[str, Any]:
    trip = turn.session.context.get(TRIP_KEY)
    return trip if isinstance(trip, dict) else {}


def _numbers(text: str | None) -> list[int]:
    return [int(raw) for raw in re.findall(r"\d{1,2}", text or "")]


def _split_names(text: str | None) -> list[str]:
    parts = re.split(r",|;|\n|\bet\b", text or "")
    return [" ".join(part.split()) for part in parts if len(part.strip()) >= 3]
