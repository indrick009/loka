"""Stock copy said in the user's voice, and never at the cost of a fact.

Observed live: "c'est combien d'avance ?", "tu me comprends même ?" and
"rappelle-moi mon budget" all received the identical "Votre demande est
enregistrée", in formal French, to a tenant writing "mola".
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from loka.bounded_contexts.ai.application.ports import Exchange, UsageEntry
from loka.bounded_contexts.ai.application.services.conversation_voice import (
    build_voice_prompt,
    keeps_the_facts,
)
from loka.bounded_contexts.ai.application.services.prompts import (
    PERSONA,
    build_analysis_prompt,
)
from loka.bounded_contexts.ai.application.use_cases.voice_platform_reply import (
    VoicePlatformReplyUseCase,
)
from loka.bounded_contexts.ai.domain.services.ai_provider import (
    LlmResponse,
    LlmUsage,
    Prompt,
)
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    FlowName,
    FlowStep,
)
from loka.shared.domain.errors import ExternalServiceUnavailable

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
WAITING = (
    "Votre recherche est bien enregistrée : Studio - Bali, Douala - "
    "150 000 FCFA/mois maximum. Je n'ai pas encore de logement à vous proposer."
)
THREAD = [
    Exchange(speaker="user", text="mon budget c'est 150 mil le mois, pas plus hein"),
    Exchange(speaker="kora", text="Votre demande est enregistrée."),
]


class TestTheFactsSurvive:
    def test_a_rewrite_with_the_same_numbers_holds(self) -> None:
        assert keeps_the_facts(
            WAITING,
            "Ah mola, c'est noté : studio à Bali, Douala, 150 000 FCFA max par mois. "
            "Je n'ai encore rien pour toi.",
        )

    def test_a_number_written_another_way_is_the_same_number(self) -> None:
        assert keeps_the_facts(WAITING, "Studio à Bali, 150.000 FCFA max, rien encore.")

    def test_a_dropped_number_throws_the_rewrite_away(self) -> None:
        assert not keeps_the_facts(WAITING, "C'est noté mola, je cherche pour toi à Bali.")

    def test_an_invented_number_throws_the_rewrite_away(self) -> None:
        assert not keeps_the_facts(
            WAITING, "Studio à Bali, 150 000 FCFA max. L'avance c'est 6 mois."
        )

    def test_a_number_the_user_already_gave_may_be_repeated(self) -> None:
        """Observed live: "ton budget c'est 150 000 FCFA" was thrown away
        because the stock sentence it replaced named no amount."""
        recorded = "Votre demande est enregistrée."

        assert keeps_the_facts(
            recorded,
            "C'est noté, 150 000 FCFA par mois max !",
            already_said=("mon budget c'est 150 mil le mois, pas plus hein",),
        )
        assert not keeps_the_facts(recorded, "C'est noté, 150 000 FCFA par mois max !")

    def test_an_empty_or_runaway_rewrite_is_refused(self) -> None:
        assert not keeps_the_facts(WAITING, "  ")
        assert not keeps_the_facts(WAITING, WAITING + " blabla" * 100)


class TestThePrompts:
    def test_the_persona_mirrors_the_register(self) -> None:
        assert "mola" in PERSONA
        assert "Pidgin" in PERSONA

    def test_the_voice_prompt_carries_the_message_the_copy_and_the_thread(self) -> None:
        prompt = build_voice_prompt(
            message="tu me comprends même ?", platform_text=WAITING, exchanges=THREAD
        )

        payload = json.loads(prompt.user)
        assert payload["user_message"] == "tu me comprends même ?"
        assert payload["platform_message"] == WAITING
        assert payload["recent_messages"][0] == {
            "from": "user",
            "text": "mon budget c'est 150 mil le mois, pas plus hein",
        }
        assert "football" in prompt.system

    def test_the_analysis_reads_what_was_said_not_only_the_labels(self) -> None:
        prompt = build_analysis_prompt(
            text="rappelle moi mon budget",
            default_language="fr",
            history=[],
            flow=FlowName.PROPERTY_SEARCH,
            step=FlowStep.AWAITING_MATCH,
            pending_question=None,
            known_facts={},
            exchanges=THREAD,
        )

        assert json.loads(prompt.user)["recent_messages"][1]["from"] == "kora"
        assert "football" in prompt.system

    def test_an_empty_thread_costs_no_tokens(self) -> None:
        prompt = build_analysis_prompt(
            text="bonjour",
            default_language="fr",
            history=[],
            flow=FlowName.SUPPORT,
            step=FlowStep.START,
            pending_question=None,
            known_facts={},
        )

        assert "recent_messages" not in json.loads(prompt.user)


class _Uow:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _Exchanges:
    async def recent(
        self, session_id: uuid.UUID, *, excluding: uuid.UUID | None = None, limit: int = 6
    ) -> list[Exchange]:
        return THREAD


class _Ledger:
    def __init__(self) -> None:
        self.entries: list[UsageEntry] = []

    async def append(self, entry: UsageEntry) -> uuid.UUID:
        self.entries.append(entry)
        return uuid.uuid4()

    async def total_cost_usd(self, *, on: object, user_id: uuid.UUID | None = None) -> Decimal:
        return sum((entry.cost_usd for entry in self.entries), Decimal("0"))


class _Writer:
    model = "test-model"

    def __init__(self, content: str | None = None, *, fails: bool = False) -> None:
        self._content = content
        self._fails = fails
        self.prompts: list[Prompt] = []

    async def generate_response(self, prompt: Prompt) -> LlmResponse:
        self.prompts.append(prompt)
        if self._fails:
            raise ExternalServiceUnavailable("down")
        return LlmResponse(
            content=self._content or "",
            usage=LlmUsage(
                input_tokens=900,
                output_tokens=60,
                latency_ms=800,
                estimated_cost_usd=0.0004,
                model=self.model,
            ),
        )


def _voice(writer: _Writer, ledger: _Ledger, uow: _Uow) -> VoicePlatformReplyUseCase:
    return VoicePlatformReplyUseCase(
        uow,  # type: ignore[arg-type]
        model=writer,
        exchanges=_Exchanges(),
        usage=ledger,
        provider="openrouter",
    )


class TestTheRewrite:
    async def test_a_faithful_rewrite_is_sent_and_paid_for(self) -> None:
        rewritten = (
            "Je te comprends bien mola ! Ta recherche est notée : studio à Bali, "
            "Douala, 150 000 FCFA max. Je n'ai encore rien pour toi."
        )
        ledger, uow = _Ledger(), _Uow()

        sent = await _voice(_Writer(rewritten), ledger, uow).execute(
            session_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            message="tu me comprends même ?",
            platform_text=WAITING,
            now=NOW,
        )

        assert sent == rewritten
        assert ledger.entries[0].use_case == "voice_platform_reply"
        assert ledger.entries[0].cost_usd == Decimal("0.0004")
        # Released before the call, committed after the ledger write.
        assert uow.commits == 2

    async def test_a_rewrite_that_invents_a_number_is_not_sent(self) -> None:
        sent = await _voice(
            _Writer("Studio à Bali, 150 000 FCFA. Et l'avance c'est 6 mois hein."),
            _Ledger(),
            _Uow(),
        ).execute(
            session_id=uuid.uuid4(),
            user_id=None,
            message="c'est combien d'avance ?",
            platform_text=WAITING,
            now=NOW,
        )

        assert sent == WAITING

    @pytest.mark.parametrize("writer", [_Writer(fails=True), _Writer("")])
    async def test_a_writer_that_fails_leaves_the_stock_sentence(self, writer: _Writer) -> None:
        sent = await _voice(writer, _Ledger(), _Uow()).execute(
            session_id=uuid.uuid4(),
            user_id=None,
            message="tu me comprends même ?",
            platform_text=WAITING,
            now=NOW,
        )

        assert sent == WAITING


class _Sequence(_Writer):
    """Answers with each sentence in turn, one per call."""

    def __init__(self, *sentences: str) -> None:
        super().__init__()
        self._sentences = list(sentences)

    async def generate_response(self, prompt: Prompt) -> LlmResponse:
        self._content = self._sentences.pop(0)
        return await super().generate_response(prompt)


class TestTheRetry:
    async def test_a_lost_number_gets_one_more_try_told_what_to_keep(self) -> None:
        """Observed live: "ta photo" for "2 photos" sent the stock copy in "vous"."""
        stock = "Photo bien reçue (2 photos) : votre annonce est en ligne."
        writer = _Sequence(
            "Super, ta photo est reçue, ton annonce est en ligne !",
            "Super, tes 2 photos sont reçues, ton annonce est en ligne !",
        )
        ledger = _Ledger()

        sent = await _voice(writer, ledger, _Uow()).execute(
            session_id=uuid.uuid4(),
            user_id=None,
            message="(photo envoyée)",
            platform_text=stock,
            now=NOW,
        )

        assert sent == "Super, tes 2 photos sont reçues, ton annonce est en ligne !"
        assert json.loads(writer.prompts[1].user)["numbers_to_keep_in_digits"] == ["2"]
        assert len(ledger.entries) == 2
