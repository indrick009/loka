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
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message import (
    AnalyseConversationMessageUseCase,
)
from loka.bounded_contexts.ai.infrastructure.openrouter import OpenRouterIntentModel
from loka.bounded_contexts.ai.infrastructure.persistence.repositories import (
    SqlAlchemyAiUsageLedger,
    SqlAlchemyConversationTurnRepository,
)
from loka.shared.application.unit_of_work import UnitOfWork
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
    return AnalyseConversationMessageUseCase(
        uow,
        inbound=uow.repository("inbound_messages"),
        sessions=uow.repository("conversation_sessions"),
        turns=SqlAlchemyConversationTurnRepository(uow.session),
        usage=SqlAlchemyAiUsageLedger(uow.session),
        model=intent_model(settings),
        budget=budget_policy(settings),
        provider_name=settings.ai.provider,
    )
