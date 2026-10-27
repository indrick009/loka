"""Everything the pipeline says to a model, and nothing it decides.

Extracted from ``analyse_conversation_message``, which had grown to carry the
orchestration, the prompts, the result rendering and the listing confirmation
at once. The split is along the line the defect report drew: this module owns
*how we ask*, the use case owns *what happens next*. A prompt change can now be
read, reviewed and tested without opening the state machine.

Two prompts live here and they share their persona, their price rule and their
topic glosses on purpose: when only one of them carried a name, the assistant
introduced itself as "Loka" on one turn and "Kasa" on another, from an account
called Kora."""

from loka.bounded_contexts.conversation.application.services.prompts.analysis import (
    build_analysis_prompt,
)
from loka.bounded_contexts.conversation.application.services.prompts.persona import (
    DIGRESSION_RULE,
    PERSONA,
)
from loka.bounded_contexts.conversation.application.services.prompts.reply import (
    build_reply_prompt,
)
from loka.bounded_contexts.conversation.application.services.prompts.sanitize import (
    collapse_repeats,
)
from loka.bounded_contexts.conversation.application.services.prompts.state import (
    exchanges_as_json,
)
from loka.bounded_contexts.conversation.application.services.prompts.topics import (
    question_topic,
)

__all__ = [
    "DIGRESSION_RULE",
    "PERSONA",
    "build_analysis_prompt",
    "build_reply_prompt",
    "collapse_repeats",
    "exchanges_as_json",
    "question_topic",
]
