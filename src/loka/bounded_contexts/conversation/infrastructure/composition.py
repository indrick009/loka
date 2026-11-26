"""AI context composition root.

Two decisions are made here and nowhere else: which model the pipeline may call,
and whether it is allowed to call it at all.

The pipeline refuses to start when it is enabled without pricing for its
configured models. A budget computed from a price nobody configured is not a
budget — it is a number that stays at zero while the bill grows, which is the
one failure mode a cost guard exists to prevent.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from loka.bounded_contexts.ai.application.decision_shadow import DecisionShadow
from loka.bounded_contexts.ai.infrastructure.agent.conversation_agent import cost_of
from loka.bounded_contexts.ai.infrastructure.failover import (
    FailoverIntentModel,
    backup_settings,
)
from loka.bounded_contexts.ai.infrastructure.jev import JevDecisionModel
from loka.bounded_contexts.ai.infrastructure.openrouter import OpenRouterIntentModel
from loka.bounded_contexts.auth.infrastructure.persistence.models import UserRow
from loka.bounded_contexts.booking.conversation.turns import BookingTurns
from loka.bounded_contexts.booking.infrastructure.seat_maps import StoredSeatMaps
from loka.bounded_contexts.booking.infrastructure.store import SqlAlchemyBookingStore
from loka.bounded_contexts.conversation.application.ports import BudgetPolicy, UsageEntry
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message import (
    AnalyseConversationMessageUseCase,
)
from loka.bounded_contexts.conversation.application.use_cases.voice_platform_reply import (
    VoicePlatformReplyUseCase,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import FlowName
from loka.bounded_contexts.conversation.infrastructure.adapters.photo_triage import (
    OpenRouterPhotoTriage,
)
from loka.bounded_contexts.conversation.infrastructure.messaging_composition import (
    CONVERSATION_SESSION_REPOSITORY,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.repositories import (
    SqlAlchemyAiUsageLedger,
    SqlAlchemyConversationExchanges,
    SqlAlchemyConversationTurnRepository,
    SqlAlchemyDecisionShadowLog,
)
from loka.bounded_contexts.dating.conversation.turns import DatingTurns
from loka.bounded_contexts.dating.domain.talk_reader import TalkLine
from loka.bounded_contexts.dating.infrastructure.face_reader import face_reader
from loka.bounded_contexts.dating.infrastructure.match_judge import OpenRouterMatchJudge
from loka.bounded_contexts.dating.infrastructure.photo_judge import OpenRouterPhotoJudge
from loka.bounded_contexts.dating.infrastructure.profile_reader import OpenRouterProfileReader
from loka.bounded_contexts.dating.infrastructure.store import SqlAlchemyDatingStore
from loka.bounded_contexts.dating.infrastructure.talk_agent import OpenRouterTalkAgent
from loka.bounded_contexts.dating.infrastructure.talk_reader import OpenRouterTalkReader
from loka.bounded_contexts.food.conversation.turns import FoodTurns
from loka.bounded_contexts.food.infrastructure.menu_reader import OpenRouterMenuReader
from loka.bounded_contexts.food.infrastructure.store import SqlAlchemyFoodStore
from loka.bounded_contexts.found.conversation.turns import FoundTurns
from loka.bounded_contexts.found.infrastructure.store import SqlAlchemyFoundStore
from loka.bounded_contexts.housing.conversation.adapters.conversation_availability import (
    ConversationAvailabilityAnswers,
    ConversationAvailabilityRequests,
)
from loka.bounded_contexts.housing.conversation.adapters.conversation_demand import (
    ConversationDemandFollowUp,
    ConversationTenantDemands,
)
from loka.bounded_contexts.housing.conversation.adapters.conversation_property_search import (
    ConversationPropertySearch,
)
from loka.bounded_contexts.housing.conversation.adapters.conversation_unlock import (
    ConversationUnlockedContacts,
    ConversationUnlockPayments,
)
from loka.bounded_contexts.housing.conversation.use_cases.reveal_unlocked_contact import (
    RevealUnlockedContactUseCase,
)
from loka.bounded_contexts.housing.infrastructure.composition import LANDLORD_DIRECTORY
from loka.bounded_contexts.payment.application.unlock_contact import UnlockContact
from loka.bounded_contexts.payment.infrastructure.mobile_money import MobileMoneyPayments
from loka.bounded_contexts.payment.infrastructure.unlock_store import SqlAlchemyUnlockStore
from loka.shared.application.context import current_context
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.infrastructure.ai_usage import RecordUsage
from loka.shared.infrastructure.composition import (
    create_property_from_conversation_use_case,
    initiate_service_fee_use_case,
    payment_gateway,
)
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from loka.shared.infrastructure.object_storage.media import S3MediaStorage


class PipelineMisconfigured(RuntimeError):
    """The AI pipeline is enabled but cannot be run safely."""


def validate_ai_settings(settings: Settings) -> None:
    """Fail at boot, not on the first tenant message.

    Raising here means a misconfigured deployment never accepts a message it
    would then answer badly, instead of discovering it in the monthly invoice.
    """
    ai = settings.ai
    if not ai.pipeline_enabled:
        return
    if not ai.api_key.get_secret_value():
        raise PipelineMisconfigured(
            "ai.pipeline_enabled is true but no OpenRouter API key is configured"
        )
    unpriced = ai.unpriced_models()
    if unpriced:
        raise PipelineMisconfigured(
            "ai.model_pricing_usd_per_million is missing entries for: "
            f"{sorted(unpriced)}; an unpriced model cannot be budgeted"
        )


def budget_policy(settings: Settings) -> BudgetPolicy:
    return BudgetPolicy(
        daily_usd=Decimal(str(settings.ai.daily_budget_usd)),
        per_user_daily_usd=Decimal(str(settings.ai.per_user_daily_budget_usd)),
    )


def intent_model(settings: Settings) -> Any | None:
    """The model, or ``None`` when the pipeline is disabled.

    Returning ``None`` rather than a broken client is deliberate: the use case
    then records the turn as UNKNOWN and asks a clarifying question, instead of
    raising on every message while the pipeline is switched off.
    """
    if not settings.ai.pipeline_enabled:
        return None
    validate_ai_settings(settings)
    primary = OpenRouterIntentModel(settings.ai)
    if not settings.ai.backup_base_url:
        return primary
    # OpenRouter out of credit or down: OmniRoute's free models answer instead
    # of the rules lane.
    return FailoverIntentModel(
        primary,
        OpenRouterIntentModel(backup_settings(settings.ai)),
        cooldown=settings.ai.backup_cooldown_seconds,
    )


def analyse_conversation_message_use_case(
    uow: UnitOfWork, *, settings: Settings
) -> AnalyseConversationMessageUseCase:
    if not isinstance(uow, SqlAlchemyUnitOfWork):
        raise TypeError("analyse_conversation_message_use_case requires a SqlAlchemyUnitOfWork")
    model = intent_model(settings)
    return AnalyseConversationMessageUseCase(
        uow,
        inbound=uow.repository("inbound_messages"),
        sessions=uow.repository("conversation_sessions"),
        turns=SqlAlchemyConversationTurnRepository(uow.session),
        usage=SqlAlchemyAiUsageLedger(uow.session),
        model=model,
        # The same OpenRouter client parses and writes: one adapter, two ports.
        # When the pipeline is off both are ``None`` and the fallbacks take over.
        response_model=model,
        budget=budget_policy(settings),
        provider_name=settings.ai.provider,
        triage=photo_triage(settings, usage_recorder(uow, settings)),
        closed=frozenset(
            FlowName(name.strip())
            for name in settings.closed_services.split(",")
            if name.strip() in FlowName.__members__
        ),
        team_phones=tuple(
            phone.strip() for phone in settings.whatsapp.alert_phones.split(",") if phone.strip()
        ),
        # The one write the analysis triggers: a confirmed listing becomes a
        # property draft. Injected here so the pipeline depends on the port.
        listing=create_property_from_conversation_use_case(uow),
        # The one read it triggers: a tenant's completed criteria become a
        # catalogue search whose results the reply prints.
        search=ConversationPropertySearch(uow),
        # The landlord's half of the availability check. Without it his
        # "c'est déjà loué" is understood and then dropped, and the lodging
        # stays in every tenant's results.
        availability=ConversationAvailabilityAnswers(
            uow, landlords=uow.repository(LANDLORD_DIRECTORY)
        ),
        # The tenant's half: his line number becomes a question to the owner of
        # the lodging he picked.
        availability_requests=ConversationAvailabilityRequests(uow),
        # A tenant's search is recorded, not answered in the thread: idia
        # acknowledges and comes back once it has something worth sending.
        demands=ConversationTenantDemands(uow),
        # A month on, his "oui"/"non" to "cherchez-vous toujours ?".
        follow_up=ConversationDemandFollowUp(uow),
        # The paid unlock: the tenant's yes becomes a withdrawal request.
        # The conversation depends on the act of asking, not on Payment's use
        # case, and settlement stays out of it — only the provider's callback
        # releases the contact.
        unlock_payments=ConversationUnlockPayments(
            uow, initiate=initiate_service_fee_use_case(uow, settings)
        ),
        # The decision-model pilot: asked on every turn, stored, never shown.
        shadow=decision_shadow(uow, settings=settings),
        # The last lines of the thread, so the model reads what was said.
        exchanges=SqlAlchemyConversationExchanges(uow.session),
        # The domains plugged into the engine: each receives its own turns.
        plugs=domain_turns(uow, settings),
        # The tenant search led by the Pydantic AI agent, when switched on.
        agent=conversation_agent(settings),
        # The forms of Food, Dating, Found and Booking, filled by the same agent.
        form_filler=form_filler(settings),
        # One agent for the whole thread, Food and the tenant search as tools.
        idia=idia_agent(settings),
    )


def conversation_agent(settings: Settings) -> Any | None:
    """The agent behind ``AI__AGENT_ENABLED``; none without a model to run it."""
    if not (settings.ai.agent_enabled and settings.ai.pipeline_enabled):
        return None
    from loka.bounded_contexts.ai.infrastructure.agent.conversation_agent import (
        PydanticTenantSearchAgent,
    )

    return PydanticTenantSearchAgent(settings.ai)


def idia_agent(settings: Settings) -> Any | None:
    """The single idia agent behind ``AI__IDIA_AGENT_ENABLED``."""
    if not (settings.ai.idia_agent_enabled and settings.ai.pipeline_enabled):
        return None
    from loka.bounded_contexts.ai.infrastructure.agent.conversation_agent import PydanticIdia

    return PydanticIdia(settings.ai)


def form_filler(settings: Settings) -> Any | None:
    """The plugged domains' form filler behind ``AI__AGENT_ENABLED``."""
    if not (settings.ai.agent_enabled and settings.ai.pipeline_enabled):
        return None
    from loka.bounded_contexts.ai.infrastructure.agent.conversation_agent import (
        PydanticFormFiller,
    )

    return PydanticFormFiller(settings.ai)


def usage_recorder(uow: SqlAlchemyUnitOfWork, settings: Settings) -> RecordUsage:
    """Writes each side model call to the ledger, inside the turn's transaction."""
    ledger = SqlAlchemyAiUsageLedger(uow.session)

    async def record(use_case: str, model: str, tokens_in: int, tokens_out: int, ms: int) -> None:
        await ledger.append(
            UsageEntry(
                use_case=use_case,
                provider=settings.ai.provider,
                model=model,
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                latency_ms=ms,
                cost_usd=cost_of(settings.ai, model, tokens_in, tokens_out),
                user_id=None,
                conversation_id=None,
                correlation_id=current_context().get("correlation_id"),
                occurred_at=datetime.now(UTC),
            )
        )

    return record


def menu_reader(
    settings: Settings, record: RecordUsage | None = None
) -> OpenRouterMenuReader | None:
    """Reads a restaurateur's menu photo; none without a model to read it."""
    if not settings.ai.pipeline_enabled or not settings.ai.api_key.get_secret_value():
        return None
    return OpenRouterMenuReader(settings.ai, S3MediaStorage(settings.object_storage), record)


def match_judge(
    settings: Settings, record: RecordUsage | None = None
) -> OpenRouterMatchJudge | None:
    """Reads dating candidates both ways; none without a model."""
    if not settings.ai.pipeline_enabled or not settings.ai.api_key.get_secret_value():
        return None
    return OpenRouterMatchJudge(settings.ai, record)


def photo_triage(
    settings: Settings, record: RecordUsage | None = None
) -> OpenRouterPhotoTriage | None:
    """Says what a photo sent with no task shows; none without a model."""
    if not settings.ai.pipeline_enabled or not settings.ai.api_key.get_secret_value():
        return None
    return OpenRouterPhotoTriage(settings.ai, S3MediaStorage(settings.object_storage), record)


def photo_judge(
    settings: Settings, record: RecordUsage | None = None
) -> OpenRouterPhotoJudge | None:
    """Looks at each dating photo; none without a model."""
    if not settings.ai.pipeline_enabled or not settings.ai.api_key.get_secret_value():
        return None
    return OpenRouterPhotoJudge(settings.ai, S3MediaStorage(settings.object_storage), record)


def talk_reader(
    settings: Settings, record: RecordUsage | None = None
) -> OpenRouterTalkReader | None:
    """Reads who a talk message is for; none without a model."""
    if not settings.ai.pipeline_enabled or not settings.ai.api_key.get_secret_value():
        return None
    return OpenRouterTalkReader(settings.ai, record)


def talk_agent(
    settings: Settings, record: RecordUsage | None = None
) -> OpenRouterTalkAgent | None:
    """The model that conducts dating talks, when ``DATING__TALK_AGENT`` is
    ``test`` or ``on`` (decision of 26 Nov 2026); none otherwise."""
    if os.environ.get("DATING__TALK_AGENT", "off").strip() not in ("test", "on"):
        return None
    if not settings.ai.pipeline_enabled or not settings.ai.api_key.get_secret_value():
        return None
    return OpenRouterTalkAgent(settings.ai, record)


def talk_agent_phones() -> frozenset[str] | None:
    """``test``: only the numbers in ``DATING__TALK_AGENT_PHONES``; ``on``: all."""
    if os.environ.get("DATING__TALK_AGENT", "off").strip() == "on":
        return None
    raw = os.environ.get("DATING__TALK_AGENT_PHONES", "+237692617975")
    return frozenset(phone.strip() for phone in raw.split(",") if phone.strip())


def profile_reader(
    settings: Settings, record: RecordUsage | None = None
) -> OpenRouterProfileReader | None:
    """Reads what a dating member is and wants; none without a model."""
    if not settings.ai.pipeline_enabled or not settings.ai.api_key.get_secret_value():
        return None
    return OpenRouterProfileReader(settings.ai, record)


def domain_turns(uow: SqlAlchemyUnitOfWork, settings: Settings) -> dict[str, Any]:
    """Food, Dating, Found and Booking, each over its own store."""

    async def first_name(user_id: uuid.UUID) -> str | None:
        name = await uow.session.scalar(select(UserRow.display_name).where(UserRow.id == user_id))
        return str(name).split()[0] if name else None

    unlock = UnlockContact(
        MobileMoneyPayments(payment_gateway(settings)), SqlAlchemyUnlockStore(uow.session)
    )
    exchanges = SqlAlchemyConversationExchanges(uow.session)
    record = usage_recorder(uow, settings)

    async def thread_of(session_id: uuid.UUID) -> list[TalkLine]:
        lines = await exchanges.recent(session_id, limit=8)
        return [
            TalkLine("member" if line.speaker == "user" else "idia", line.text) for line in lines
        ]

    return {
        "food": FoodTurns(
            SqlAlchemyFoodStore(uow.session),
            name_of=first_name,
            reader=menu_reader(settings, record),
        ),
        "dating": DatingTurns(
            SqlAlchemyDatingStore(uow.session),
            unlock=unlock,
            judge=match_judge(settings, record),
            photos=photo_judge(settings, record),
            reader=talk_reader(settings, record),
            thread_of=thread_of,
            profiles=profile_reader(settings, record),
            faces=face_reader(S3MediaStorage(settings.object_storage)),
            talk_agent=talk_agent(settings, record),
            talk_agent_phones=talk_agent_phones(),
        ),
        "found": FoundTurns(SqlAlchemyFoundStore(uow.session), unlock=unlock),
        "booking": BookingTurns(
            SqlAlchemyBookingStore(uow.session),
            payments=MobileMoneyPayments(payment_gateway(settings)),
            maps=StoredSeatMaps(S3MediaStorage(settings.object_storage)),
        ),
    }


def voice_platform_reply_use_case(
    uow: UnitOfWork, *, settings: Settings
) -> VoicePlatformReplyUseCase | None:
    """The rewriter of stock copy, or ``None`` when no model may be called."""
    if not isinstance(uow, SqlAlchemyUnitOfWork):
        raise TypeError("voice_platform_reply_use_case requires a SqlAlchemyUnitOfWork")
    model = intent_model(settings)
    if model is None:
        return None
    return VoicePlatformReplyUseCase(
        uow,
        model=model,
        exchanges=SqlAlchemyConversationExchanges(uow.session),
        usage=SqlAlchemyAiUsageLedger(uow.session),
        provider=settings.ai.provider,
    )


def reveal_unlocked_contact_use_case(uow: UnitOfWork) -> RevealUnlockedContactUseCase:
    """The tenant's half of settlement: hand over number and location once paid.

    Not part of the analysis pipeline: it runs on the provider's callback, from
    a consumer, and its two dependencies are the session the tenant last used
    and the adapter that assembles the contact from other contexts.
    """
    if not isinstance(uow, SqlAlchemyUnitOfWork):
        raise TypeError("reveal_unlocked_contact_use_case requires a SqlAlchemyUnitOfWork")
    sessions = uow.repository(CONVERSATION_SESSION_REPOSITORY)
    return RevealUnlockedContactUseCase(
        uow,
        sessions,
        contacts=ConversationUnlockedContacts(
            uow, sessions=sessions, landlords=uow.repository(LANDLORD_DIRECTORY)
        ),
    )


def decision_shadow(uow: SqlAlchemyUnitOfWork, *, settings: Settings) -> DecisionShadow | None:
    """The pilot, or ``None`` when it is off or has no key to call with."""
    ai = settings.ai
    if not ai.decision_shadow_enabled or not ai.api_key.get_secret_value():
        return None
    return DecisionShadow(
        uow,
        model=JevDecisionModel(ai),
        log=SqlAlchemyDecisionShadowLog(uow.session),
        usage=SqlAlchemyAiUsageLedger(uow.session),
        provider=ai.provider,
        timeout_seconds=ai.decision_timeout_seconds,
    )
