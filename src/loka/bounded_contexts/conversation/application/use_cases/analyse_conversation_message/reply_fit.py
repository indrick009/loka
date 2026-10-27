"""Whether the sentence written with the analysis asks what was decided."""

from __future__ import annotations

from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.flow_rules import (  # noqa: E501
    field_for_step,
    is_search,
    target_flow,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
    Verdict,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
    FlowName,
    FlowStep,
)

# Steps whose words the inline writer is never in a position to choose well,
# but whose sentence the platform replaces anyway, or that the analysis prompt
# tells it how to handle ("if none is missing, confirm the summary").
_STEPS_THE_WRITER_MAY_REACH = frozenset({FlowStep.CONFIRM_PROPERTY, FlowStep.PRESENT_RESULTS})


def inline_reply_fits(
    prompt_flow: FlowName,
    *,
    session: ConversationSession,
    verdict: Verdict,
    asking: str | None = None,
) -> bool:
    """Whether the sentence written with the analysis asks what was decided.

    The inline writer only ever sees one form and picks its next missing field;
    the backend decides afterwards and may land elsewhere. Two ways it did:

    * the other side's form. "I'm looking for a modern studio in Bastos"
      missed the rules, the model was shown the landlord's fields, and the
      tenant was asked how many identical lodgings his listing offers;
    * a step outside any form. The backend asked "publier ou chercher ?" and
      the sentence asked for the property type, so the user answered a
      question the session was not waiting for.

    Either way the sentence is dropped and written again against the decision.

    A refused turn is a third way: the model read "Juste un seul" with no
    confidence, the turn was refused and the quantity kept open, yet its
    sentence had already moved on to the rent. The landlord's next number
    would have been filed against a question he no longer thought was open.

    A turn that recorded no field of an open form question is a fourth: the
    writer answered as if the fact had been handed over, so its sentence asks
    the *following* question while the session still waits for the current one.

    A fifth is the same drift with a field recorded: the writer moved past a
    field it did not fill. The writer now names the field it asks about
    (``asking``); when that name is not the field the backend kept open, the
    sentence asked the wrong question and is written again.
    """
    if not verdict.accepted:
        return False
    target = target_flow(session, verdict.intent)
    if is_search(prompt_flow) is not is_search(target):
        return False
    step = verdict.next_step
    if step is None:
        # Nothing moved while a form question is open, and nothing was
        # recorded: the sentence cannot be about an answer. Observed live:
        # "gardien oui parking non" came back a bare AFFIRMATIVE, and the
        # writer asked an availability date the form never asks, then told the
        # landlord "je publie" while the session still waited on the guard.
        open_field = field_for_step(session.step, target)
        return open_field is None or bool(verdict.facts.facts)
    if step in _STEPS_THE_WRITER_MAY_REACH:
        return True
    field = field_for_step(step, target)
    if field is None:
        return False
    # A form step, and the turn recorded nothing: the inline writer only ever
    # saw the message, so it may have *read* a fact it failed to hand over and
    # written the question *after* the one the backend kept open. Observed live:
    # a landlord's "elle sont inclus" produced no charges fact, the writer moved
    # on to the deposit, and the session stayed on the charges — so his later
    # "aucune", meant for the minimum duration, was filed as "no charges" and
    # the published listing said the water was charged separately. With nothing
    # recorded there is nothing to phrase an answer around, so the question the
    # backend decided is the one asked again.
    if not verdict.facts.facts:
        return False
    # The writer that said which field its sentence asks about is held to it:
    # a name other than the open field means the sentence moved on, and the
    # record and the sentence would describe two different questions. A writer
    # that stayed silent keeps today's behaviour — the sentence is used, as the
    # guard cannot contradict it.
    return asking is None or asking == field
