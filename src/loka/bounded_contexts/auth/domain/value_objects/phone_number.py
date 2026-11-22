"""Phone number value object.

WhatsApp is the identity anchor of the platform, so normalisation is not
cosmetic: the same human typing ``+237 6 99 12 34 56`` or ``699123456``
must map to exactly one account.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import phonenumbers

from loka.shared.domain.value_object import ValueObject

_DIGITS = re.compile(r"\d+")

DEFAULT_COUNTRY_CODE = "+237"


def _parsed(raw: str, region: str | None) -> phonenumbers.PhoneNumber | None:
    """libphonenumber's reading, or None when no country has such a number.

    « Possible » rather than « valid »: the length is checked per country, the
    ranges are not, so a newly opened Cameroonian range is not refused before
    the library learns it.
    """
    try:
        number = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        return None
    return number if phonenumbers.is_possible_number(number) else None


@dataclass(frozen=True, slots=True)
class PhoneNumber(ValueObject):

    e164: str

    def __post_init__(self) -> None:
        # Every country is served (22 Nov 2026); libphonenumber knows each
        # one's lengths.
        if not self.e164.startswith("+"):
            self._reject("phone number must be in E.164 format", value=self.e164)
        number = _parsed(self.e164, None)
        if number is None:
            self._reject("phone number length is implausible", value=self.e164)
            return
        object.__setattr__(
            self, "e164", phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
        )

    @classmethod
    def normalize(
        cls, raw: str, *, default_country_code: str = DEFAULT_COUNTRY_CODE
    ) -> PhoneNumber:
        """Turn user input into E.164.

        A local input such as ``612 22 33 44`` gets the default country code.
        An input that already carries a country code (``+`` or ``00``) keeps
        it: silently turning ``+33612345678`` into a Cameroonian number would
        store an unreachable contact.
        """
        text = raw.strip()
        if not _DIGITS.search(text):
            raise cls._violation("phone number is empty", value=raw)
        if text.startswith("00"):
            text = "+" + text[2:]
        code = int(default_country_code.lstrip("+"))
        digits = "".join(_DIGITS.findall(text))
        if not text.startswith("+") and digits.startswith(str(code)) and len(digits) > 9:
            # « 237699123456 »: the country code typed without its « + ».
            text = "+" + digits
        region = phonenumbers.region_code_for_country_code(code)
        number = _parsed(text, None if text.startswith("+") else region)
        if number is None:
            raise cls._violation("phone number length is implausible", value=raw)
        return cls(e164=phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164))

    @property
    def _number(self) -> phonenumbers.PhoneNumber:
        return phonenumbers.parse(self.e164, None)

    @property
    def national_number(self) -> str:
        return str(self._number.national_number)

    @property
    def country_code(self) -> str:
        return str(self._number.country_code)

    def masked(self) -> str:
        tail = self.national_number[-4:]
        return f"{self.e164[: -len(tail)]}***{tail}"

    def whatsapp_jid(self) -> str:
        """Baileys expects digits@s.whatsapp.net."""
        return f"{''.join(_DIGITS.findall(self.e164))}@s.whatsapp.net"

    def obfuscate(self, *, keep_last: int = 2) -> str:
        digits = "".join(_DIGITS.findall(self.e164))
        return digits[:-keep_last] + "*" * keep_last if keep_last else "*" * len(digits)