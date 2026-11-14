"""What a reply can show beyond its words, said once for every WhatsApp channel.

The conversation writes the sentence it always wrote, and may add three things:

- ``options``: the answers it expects. Each option's ``id`` is exactly what the
  user would type (« 1 », « OUI »), so a tapped button and a typed answer read
  the same downstream. The official API shows them as buttons (three at most)
  or as a list (ten at most); Baileys sends the sentence alone, which already
  says « réponds 1 ».
- ``contact``: a contact card the user can save in one tap.
- ``place``: a map pin with the place's name and address.

The sentence always stands on its own: a channel that can show nothing more
still says everything. That is what lets Baileys carry the tests today and the
official API take over without a word of the conversation changing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MAX_OPTIONS = 10


@dataclass(frozen=True, slots=True)
class Option:
    id: str
    title: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class ContactCard:
    name: str
    phone: str


@dataclass(frozen=True, slots=True)
class Place:
    latitude: float
    longitude: float
    name: str | None = None
    address: str | None = None


@dataclass(frozen=True, slots=True)
class RichReply:
    options: tuple[Option, ...] = ()
    # The label of the button that opens the list, when there are more than three.
    menu: str | None = None
    contact: ContactCard | None = None
    place: Place | None = None

    def __post_init__(self) -> None:
        if len(self.options) > MAX_OPTIONS:
            raise ValueError(f"a reply offers at most {MAX_OPTIONS} options")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.options:
            out["options"] = [
                {"id": o.id, "title": o.title, "description": o.description}
                for o in self.options
            ]
        if self.menu:
            out["menu"] = self.menu
        if self.contact:
            out["contact"] = {"name": self.contact.name, "phone": self.contact.phone}
        if self.place:
            out["place"] = {
                "latitude": self.place.latitude,
                "longitude": self.place.longitude,
                "name": self.place.name,
                "address": self.place.address,
            }
        return out

    @classmethod
    def from_dict(cls, raw: Any) -> RichReply | None:
        """The reply carried on the bus; ``None`` for nothing or for a shape it cannot read."""
        if not isinstance(raw, dict) or not raw:
            return None
        try:
            options = tuple(
                Option(str(o["id"]), str(o["title"]), o.get("description") or None)
                for o in raw.get("options") or ()
            )
            contact = raw.get("contact")
            place = raw.get("place")
            return cls(
                options=options,
                menu=raw.get("menu") or None,
                contact=ContactCard(str(contact["name"]), str(contact["phone"]))
                if isinstance(contact, dict)
                else None,
                place=Place(
                    float(place["latitude"]),
                    float(place["longitude"]),
                    place.get("name") or None,
                    place.get("address") or None,
                )
                if isinstance(place, dict)
                else None,
            )
        except (KeyError, TypeError, ValueError):
            return None
