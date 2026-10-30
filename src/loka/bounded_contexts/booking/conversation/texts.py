"""Every sentence Booking writes itself. Times, prices and seats are the agency's."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from loka.bounded_contexts.booking.domain.line import DOUALA
from loka.bounded_contexts.booking.domain.reservation import Reservation
from loka.bounded_contexts.conversation.application.plugs.forms import xaf


def when(moment: datetime) -> str:
    return moment.astimezone(DOUALA).strftime("%d/%m à %Hh%M")


def departures(trips: Sequence[dict[str, Any]], origin: str, destination: str) -> str:
    lines = "\n".join(
        f"{index}. {trip['agency']} — {trip['time']} — {xaf(int(trip['price']))} "
        f"({trip['class'].lower()}, {trip['left']} places libres)"
        for index, trip in enumerate(trips, start=1)
    )
    return (
        f"Voici les départs {origin} → {destination} :\n{lines}\n\n"
        "Réponds avec le numéro du départ qui te convient."
    )


def no_departure(origin: str, destination: str, day: str) -> str:
    return (
        f"Je n'ai aucun départ {origin} → {destination} avec assez de places le {day}. "
        "Essaie une autre date ?"
    )


def pick_a_number(count: int) -> str:
    return f"Réponds avec le numéro du départ, entre 1 et {count}."


def plan(passengers: int) -> str:
    places = "ta place" if passengers == 1 else f"tes {passengers} places"
    return (
        f"Voici le plan du bus : les places vertes sont libres. Choisis {places} en "
        "répondant avec le(s) numéro(s), par exemple « 12 »"
        + (" ou « 12 et 13 »." if passengers > 1 else ".")
    )


def plan_as_text(free: Sequence[int]) -> str:
    shown = ", ".join(str(seat) for seat in free[:40])
    return f"Places libres : {shown}."


def wrong_seats(passengers: int, taken: Sequence[int]) -> str:
    detail = f" Déjà prises : {', '.join(map(str, taken))}." if taken else ""
    return (
        f"Il me faut {passengers} numéro(s) de place libres, pris sur le plan.{detail} "
        "Réessaie avec d'autres numéros."
    )


def names(passengers: int) -> str:
    if passengers == 1:
        return "Places bloquées 15 minutes. Quel est le nom complet du voyageur, comme sur la CNI ?"
    return (
        "Places bloquées 15 minutes. Donne le nom complet de chaque voyageur, comme sur "
        "la CNI, séparés par des virgules."
    )


def pay(reservation: Reservation, phone: str) -> str:
    seats = ", ".join(map(str, reservation.seats))
    return (
        f"Récapitulatif : place(s) {seats}, départ le {when(reservation.departs_at)}, "
        f"total {xaf(reservation.amount_xaf)}.\n"
        f"Je lance le paiement Mobile Money sur ce numéro ({phone}) ? Réponds OUI, "
        "envoie le numéro MTN ou Orange à utiliser, ou NON pour annuler."
    )


def payment_sent(amount: int, phone: str) -> str:
    return (
        f"J'ai envoyé la demande de paiement de {xaf(amount)} au {phone}. Valide-la sur "
        "ton téléphone avec ton code Mobile Money ; ta place reste bloquée en attendant."
    )


def payment_refused() -> str:
    return "Le paiement n'a pas pu être lancé. Envoie un autre numéro Mobile Money, ou réessaie."


def cancelled() -> str:
    return "D'accord, j'ai annulé la réservation et libéré les places."


def ticket(reservation: Reservation, agency: str, origin: str, destination: str) -> str:
    seats = ", ".join(map(str, reservation.seats))
    travellers = ", ".join(reservation.passengers)
    return (
        f"Réservation confirmée ! Référence : {reservation.reference}\n"
        f"{agency} — {origin} → {destination}\n"
        f"Départ : {when(reservation.departs_at)}\n"
        f"Place(s) : {seats}\nVoyageur(s) : {travellers}\n"
        f"Payé : {xaf(reservation.amount_xaf)}\n"
        "Présente cette référence et ta CNI au guichet au moins 30 minutes avant le départ."
    )


def to_agency(reservation: Reservation, origin: str, destination: str) -> str:
    seats = ", ".join(map(str, reservation.seats))
    return (
        f"Nouvelle réservation Kora {reservation.reference} : {origin} → {destination}, "
        f"départ le {when(reservation.departs_at)}, place(s) {seats}, "
        f"{', '.join(reservation.passengers)}. Payé : {xaf(reservation.amount_xaf)}."
    )


def lapsed() -> str:
    return (
        "Le paiement n'a pas été validé à temps : les places ont été libérées. Tu peux "
        "recommencer quand tu veux."
    )
