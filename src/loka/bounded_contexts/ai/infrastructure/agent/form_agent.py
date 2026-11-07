"""The form of any plugged domain, filled by the model with the domain's rules as a tool.

Food, Dating, Found and Booking each declare a ``DomainSpec``: who the user is,
the rules, the fields in order and how a fact is validated. The agent reads
the message, records what it understood through ``record_facts`` — validated
by the domain, a refusal reported back so it asks again in other words — and
writes the reply asking the next field. What the domain then does with the
facts (search, propose, book, pay) stays in its own turns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model

from loka.bounded_contexts.conversation.application.plugs.spec import DomainSpec
from loka.bounded_contexts.conversation.domain.entities.conversation_session import FlowName

DOUALA = timezone(timedelta(hours=1))
# Where a place is given, in every domain: the spec reads them through
# ``common_facts``.
PLACE_KEYS = ("city", "neighbourhood")


class FormReply(BaseModel):
    act: Literal["answer", "question", "social", "other_service", "unclear"] = Field(
        description=(
            "answer: the message gives facts for the form; question: the user asks "
            "something; social: greeting, thanks, small talk; other_service: the user "
            "wants another Kora service; unclear: you cannot tell."
        )
    )
    language: Literal["fr", "en"] = Field(
        description="The user's language; fewer than three words keeps the session language."
    )
    reply: str = Field(
        description="The WhatsApp message Kora sends: short, warm, one question at most."
    )
    asking: str | None = Field(
        default=None,
        description="The name of the form field your reply asks for, or null.",
    )


@dataclass(slots=True)
class FormDeps:
    spec: DomainSpec
    flow: FlowName
    facts: dict[str, Any]
    open_field: str | None
    language: str
    # Every entity a record_facts call passed, validated again by the pipeline.
    entities: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FormTurn:
    act: str
    reply: str
    asking: str | None
    language: str
    entities: dict[str, Any]
    input_tokens: int
    output_tokens: int


_INSTRUCTIONS = """\
You are Kora, a WhatsApp assistant in Cameroon: housing, food, dating, found \
documents and bus tickets. Talk like a person, never like a form.
- Read what the user really says: a greeting gets a greeting, a remark or a \
complaint gets a real answer, a question about how the service works gets a \
short honest answer, then come back to the form.
- Every fact you understand goes through record_facts, all at once when one \
message gives several, with the field names given below. Only facts the tool \
accepted are known; if it refuses one, ask for it again in other words.
- Ask only for the next missing field, in your own words, one question per \
message, and set asking to its name. A field marked "Kora adds" is followed by \
that text, added by Kora: never say it yourself.
- Never invent a fact, a price, a place or a person. Never claim to have no \
memory: the stored facts are your memory. Never repeat a sentence already sent.
- When the user wants another Kora service, set act to other_service and \
leave reply empty.
- Plain WhatsApp text, no markdown, at most one emoji.
"""


class FormAgent:
    def __init__(self, model: Model | str, *, retries: int = 2) -> None:
        self._agent: Agent[FormDeps, FormReply] = Agent(
            model,
            deps_type=FormDeps,
            output_type=FormReply,
            instructions=_INSTRUCTIONS,
            retries=retries,
        )
        self._register(self._agent)

    async def reply(
        self,
        text: str,
        *,
        spec: DomainSpec,
        flow: FlowName,
        facts: dict[str, Any],
        open_field: str | None,
        language: str,
        history: list[ModelMessage],
    ) -> FormTurn:
        deps = FormDeps(
            spec=spec, flow=flow, facts=dict(facts), open_field=open_field, language=language
        )
        result = await self._agent.run(text, deps=deps, message_history=history)
        usage = result.usage
        return FormTurn(
            act=result.output.act,
            reply=result.output.reply.strip(),
            asking=result.output.asking,
            language=result.output.language,
            entities=deps.entities,
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
        )

    @staticmethod
    def _register(agent: Agent[FormDeps, FormReply]) -> None:
        @agent.instructions
        def state(ctx: RunContext[FormDeps]) -> str:
            deps = ctx.deps
            known = {**deps.facts, **deps.spec.validate(dict(deps.entities), deps.flow).facts}
            fields = []
            for item in deps.spec.form(deps.flow):
                line = f"- {item.name}: {item.topic}"
                notice = item.warn(deps.language)
                if notice and known.get(item.name) in (None, "", []):
                    line += f' (Kora adds: "{notice}")'
                fields.append(line)
            missing = [item.name for item in deps.spec.missing(deps.flow, known)]
            names = sorted({*deps.spec.entities, *PLACE_KEYS})
            language = "English" if deps.language == "en" else "French"
            return "\n".join(
                [
                    f"The user is {deps.spec.roles[deps.flow]}.",
                    f"Today: {datetime.now(DOUALA).date().isoformat()}.",
                    f"Session language: {language}.",
                    "Domain rules:" + deps.spec.rules,
                    "The form, in order:",
                    *fields,
                    f"Field names record_facts accepts: {', '.join(names)}. A place is "
                    "given as city and neighbourhood.",
                    f"Stored facts: {known or 'none yet'}.",
                    f"The question open before this message: {deps.open_field or 'none'}.",
                    f"Still missing, in order: {', '.join(missing) or 'nothing'}.",
                ]
            )

        @agent.tool
        def record_facts(ctx: RunContext[FormDeps], facts: dict[str, Any]) -> dict[str, Any]:
            """Store what the user said, under the field names listed. Only facts he gave.

            Args:
                facts: field name to value, e.g. {"first_name": "Indrick", "age": 26}.
            """
            deps = ctx.deps
            merged = {**deps.entities, **facts}
            checked = deps.spec.validate(dict(merged), deps.flow)
            deps.entities = merged
            known = {**deps.facts, **checked.facts}
            missing = [item.name for item in deps.spec.missing(deps.flow, known)]
            return {
                "accepted": sorted(name for name in checked.facts if name not in deps.facts),
                "rejected": dict(checked.rejected),
                "still_missing": missing,
                "next_field": missing[0] if missing else None,
            }
