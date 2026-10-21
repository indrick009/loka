"""AI context composition root.

Two decisions are made here and nowhere else: which model the pipeline may call,
and whether it is allowed to call it at all.

The pipeline refuses to start when it is enabled without pricing for its
configured models. A budget computed from a price nobody configured is not a
budget — it is a number that stays at zero while the bill grows, which is the
one failure mode a cost guard exists to prevent.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from loka.bounded_contexts.ai.application.ports import BudgetPolicy
from loka.bounded_contexts.ai.application.services.decision_shadow import DecisionShadow
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message import (
    AnalyseConversationMessageUseCase,
)
from loka.bounded_contexts.ai.application.use_cases.reveal_unlocked_contact import (
    RevealUnlockedContactUseCase,
)
from loka.bounded_contexts.ai.application.use_cases.voice_platform_reply import (
    VoicePlatformReplyUseCase,
)
from loka.bounded_contexts.ai.infrastructure.adapters.conversation_availability import (
    ConversationAvailabilityAnswers,
    ConversationAvailabilityRequests,
)
from loka.bounded_contexts.ai.infrastructure.adapters.conversation_demand import (
    ConversationTenantDemands,
)
from loka.bounded_contexts.ai.infrastructure.adapters.conversation_property_search import (
    ConversationPropertySearch,
)
from loka.bounded_contexts.ai.infrastructure.adapters.conversation_unlock import (
    ConversationUnlockedContacts,
    ConversationUnlockPayments,
)
from loka.bounded_contexts.ai.infrastructure.jev import JevDecisionModel
from loka.bounded_contexts.ai.infrastructure.openrouter import OpenRouterIntentModel
from loka.bounded_contexts.ai.infrastructure.persistence.repositories import (
    SqlAlchemyAiUsageLedger,
    SqlAlchemyConversationExchanges,
    SqlAlchemyConversationTurnRepository,
    SqlAlchemyDecisionShadowLog,
)
from loka.bounded_contexts.landlord.infrastructure.composition import (
    LANDLORD_DIRECTORY,
)
from loka.bounded_contexts.messaging.infrastructure.composition import (
    CONVERSATION_SESSION_REPOSITORY,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.infrastructure.composition import (
    create_property_from_conversation_use_case,
    initiate_service_fee_use_case,
)
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


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
    return OpenRouterIntentModel(settings.ai)


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
        # A tenant's search is recorded, not answered in the thread: Kora
        # acknowledges and comes back once it has something worth sending.
        demands=ConversationTenantDemands(uow),
        # The 1 000 FCFA unlock: the tenant's yes becomes a withdrawal request.
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
    )


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
