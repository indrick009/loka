"""The bus agencies' dashboard: register, add lines, follow reservations.

An agency signs in with its WhatsApp number and the access key it was given at
registration (also sent to it on WhatsApp when that number has written to
Kora). Only the key's digest is stored.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from loka.bounded_contexts.auth.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.booking.conversation.spec import city
from loka.bounded_contexts.booking.conversation.texts import when
from loka.bounded_contexts.booking.domain.line import Agency, Line
from loka.bounded_contexts.booking.infrastructure.store import SqlAlchemyBookingStore
from loka.bounded_contexts.conversation.application.plugs.desk import ConversationDesk
from loka.bounded_contexts.conversation.infrastructure.messaging_composition import (
    CONVERSATION_SESSION_REPOSITORY,
)
from loka.interfaces.http.container import get_container
from loka.shared.domain.errors import AuthorizationDenied, ValidationFailed
from loka.shared.infrastructure.composition import unit_of_work

router = APIRouter(tags=["agencies"])
DEFAULT_COMMISSION_XAF = 200
_PAGE = (Path(__file__).parent.parent / "pages" / "agency.html").read_text(encoding="utf-8")


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _phone(raw: str) -> str:
    try:
        return PhoneNumber.normalize(raw).e164
    except Exception as exc:
        raise ValidationFailed("numéro de téléphone invalide") from exc


class Registration(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    phone: str = Field(min_length=8, max_length=20)


class NewLine(BaseModel):
    origin: str = Field(min_length=2, max_length=60)
    destination: str = Field(min_length=2, max_length=60)
    bus_class: str = "CLASSIQUE"
    price_xaf: int
    seats: int
    layout: str = "2+2"
    times: list[str]


@router.get("/agences", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> str:
    return _PAGE


@router.post("/agencies/register", status_code=status.HTTP_201_CREATED)
async def register(body: Registration, request: Request) -> dict[str, str]:
    phone, key = _phone(body.phone), secrets.token_urlsafe(9)
    now = datetime.now(UTC)
    async with unit_of_work(get_container(request).database) as uow:
        store = SqlAlchemyBookingStore(uow.session)
        if await store.agency_by_phone(phone) is not None:
            raise ValidationFailed("une agence existe déjà avec ce numéro")
        agency = Agency(
            uuid.uuid4(), " ".join(body.name.split()), phone, _digest(key),
            DEFAULT_COMMISSION_XAF, now,
        )
        await store.add_agency(agency)
        desk = ConversationDesk(uow, uow.repository(CONVERSATION_SESSION_REPOSITORY))
        await desk.notify_phone(
            phone,
            f"Bienvenue sur Kora Booking, {agency.name} ! Votre clé d'accès au tableau de "
            f"bord : {key}. Gardez-la, elle ne sera plus affichée.",
            now=now,
        )
        await uow.commit()
    return {"agency_id": str(agency.agency_id), "key": key, "phone": phone}


async def _agency(
    store: SqlAlchemyBookingStore, phone: str | None, key: str | None
) -> Agency:
    agency = await store.agency_by_phone(_phone(phone or ""))
    if agency is None or not key or not secrets.compare_digest(agency.key_digest, _digest(key)):
        raise AuthorizationDenied("numéro ou clé incorrect")
    return agency


def _line(line: Line) -> dict[str, Any]:
    return {
        "line_id": str(line.line_id),
        "origin": line.origin,
        "destination": line.destination,
        "bus_class": line.bus_class,
        "price_xaf": line.price_xaf,
        "seats": line.seats,
        "layout": line.layout,
        "times": list(line.times),
    }


@router.get("/agencies/me")
async def me(
    request: Request,
    x_agency_phone: Annotated[str | None, Header()] = None,
    x_agency_key: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    async with unit_of_work(get_container(request).database) as uow:
        store = SqlAlchemyBookingStore(uow.session)
        agency = await _agency(store, x_agency_phone, x_agency_key)
        lines = await store.lines_of(agency.agency_id)
        reservations = await store.reservations_of(agency.agency_id)
    by_line = {line.line_id: line for line in lines}
    return {
        "name": agency.name,
        "phone": agency.phone,
        "commission_xaf": agency.commission_xaf,
        "lines": [_line(line) for line in lines],
        "reservations": [
            {
                "reference": item.reference,
                "route": f"{by_line[item.line_id].origin} → {by_line[item.line_id].destination}"
                if item.line_id in by_line
                else "",
                "departs": when(item.departs_at),
                "seats": item.seats,
                "passengers": item.passengers,
                "amount_xaf": item.amount_xaf,
                "commission_xaf": item.commission_xaf,
                "status": item.status,
            }
            for item in reservations
        ],
    }


@router.post("/agencies/lines", status_code=status.HTTP_201_CREATED)
async def add_line(
    body: NewLine,
    request: Request,
    x_agency_phone: Annotated[str | None, Header()] = None,
    x_agency_key: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    async with unit_of_work(get_container(request).database) as uow:
        store = SqlAlchemyBookingStore(uow.session)
        agency = await _agency(store, x_agency_phone, x_agency_key)
        line = Line.create(
            agency_id=agency.agency_id,
            origin=city(body.origin) or body.origin,
            destination=city(body.destination) or body.destination,
            bus_class=body.bus_class.upper(),
            price_xaf=body.price_xaf,
            seats=body.seats,
            layout=body.layout,
            times=body.times,
        )
        await store.add_line(line)
        await uow.commit()
    return _line(line)
