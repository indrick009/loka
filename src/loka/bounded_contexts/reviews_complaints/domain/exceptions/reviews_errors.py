"""Reviews & Complaints context errors."""

from __future__ import annotations

from loka.shared.domain.errors import DomainError


class NoStandingToSpeak(DomainError):
    """The author was never introduced to this provider by Kora.

    The gate that keeps the ranking and the sanction queue honest: without it
    anybody could rate his own listing to the top, or file complaints until a
    competitor is hidden.
    """

    code = "no_standing_to_speak"


class AlreadySpoken(DomainError):
    """This introduction has already produced a review, or a complaint.

    One introduction, one voice. Allowing a second would let one person reach
    the hide threshold alone, and let one tenant's enthusiasm outweigh everyone
    else's in the ranking.
    """

    code = "already_spoken"


class SanctionRequiresHuman(DomainError):
    """Something tried to ban a provider without a named reviewer.

    Kora may hide; only a person bans (§6). This fires when a caller reaches the
    ban path with no reviewer id, which would otherwise record an anonymous
    decision nobody can be asked about.
    """

    code = "sanction_requires_human"
