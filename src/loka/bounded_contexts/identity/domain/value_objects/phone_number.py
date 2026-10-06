"""Phone number value object.

WhatsApp is the identity anchor of the platform, so normalisation is not
cosmetic: the same human typing ``+237 6 99 12 34 56`` or ``699123456``
must map to exactly one account.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from loka.shared.domain.value_object import ValueObject

_DIGITS = re.compile(r"\d+")
_ALLOWED_PREFIXES = ("+237", "+225", "+223", "+242", "+221", "+233", "+254")

DEFAULT_COUNTRY_CODE = "+237"


@dataclass(frozen=True, slots=True)
class PhoneNumber(ValueObject):

    e164: str

    def __post_init__(self) -> None:
        if not self.e164.startswith("+"):
            self._reject("phone number must be in E.164 format", value=self.e164)
        digits = "".join(_DIGITS.findall(self.e164))
        if not 9 <= len(digits) <= 13:
            self._reject("phone number length is implausible", value=self.e164)
        if not any(self.e164.startswith(prefix) for prefix in _ALLOWED_PREFIXES):
            self._reject("unsupported country code", value=self.e164)
        object.__setattr__(self, "e164", f"+{digits}")

    @classmethod
    def normalize(
        cls, raw: str, *, default_country_code: str = DEFAULT_COUNTRY_CODE
    ) -> PhoneNumber:
        """Turn user input into E.164.

        A local input such as ``612 22 33 44`` gets the default country code.
        An input that already carries a country code (it starts with ``+`` or
        ``00``) is never rewritten: silently turning ``+33612345678`` into a
        Cameroonian number would store an unreachable contact.
        """
        digits = "".join(_DIGITS.findall(raw))
        if not digits:
            raise cls._violation("phone number is empty", value=raw)
        has_explicit_country_code = digits.startswith("00") or raw.strip().startswith("+")
        if digits.startswith("00"):
            digits = digits[2:]
        country_code = default_country_code.lstrip("+")
        if not digits.startswith(country_code):
            if has_explicit_country_code:
                raise cls._violation(
                    "unsupported country code for this deployment",
                    value=raw,
                    default_country_code=country_code,
                )
            digits = country_code + digits.lstrip("0")
        return cls(e164=f"+{digits}")

    @property
    def national_number(self) -> str:
        return "".join(_DIGITS.findall(self.e164))[-9:]

    @property
    def country_code(self) -> str:
        return "".join(_DIGITS.findall(self.e164))[:3]

    def masked(self) -> str:
        tail = self.national_number[-4:]
        return f"{self.e164[: -len(tail)]}***{tail}"

    def whatsapp_jid(self) -> str:
        """Baileys expects digits@s.whatsapp.net."""
        return f"{''.join(_DIGITS.findall(self.e164))}@s.whatsapp.net"

    def obfuscate(self, *, keep_last: int = 2) -> str:
        digits = "".join(_DIGITS.findall(self.e164))
        return digits[:-keep_last] + "*" * keep_last if keep_last else "*" * len(digits)