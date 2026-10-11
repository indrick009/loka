---
name: kora-conversation-engine
description: >-
  The multi-domain conversational contract for Kora — dialogue acts, task
  lifecycle, topic switching, reply grounding, failure handling, and the
  WhatsApp channel rules that Housing, Food, Dating and Found all depend on.
  Use this skill whenever you touch how Kora talks to a user: intent handling,
  conversation session or flow logic, the deterministic classifier, fact
  extraction, prompts, outbound replies, or the Baileys gateway. Use it too
  when grafting a new domain onto the single WhatsApp number, and whenever a
  conversation misbehaves — repeats a question, answers with a generic
  acknowledgement, forgets a stored fact, drifts between landlord and tenant,
  invents a number, or goes silent. Read it before changing anything in the ai
  or messaging bounded contexts, even for a one-line fix, because most
  conversational defects in Kora trace back to one of these rules being
  bypassed.
metadata:
  origin: Kora
  scope: cross-cutting
---

# Kora Conversation Engine

One WhatsApp number, one conversation, four business domains behind it. The user
never names a module — they say what they want and Kora works it out.

## Read these first, they are the existing baseline

This skill deliberately does **not** restate what the repository already
documents. Read the relevant one before changing code:

- **`docs/conversational-engine.md`** — the question order for Housing
  (`PROPERTY_REQUIREMENTS`, `SEARCH_REQUIREMENTS`), the code/LLM trust boundary,
  location resolution, and the **token-reduction strategy already implemented**
  and locked by `test_the_prompt_omits_context_that_can_no_longer_be_used`.
  Never re-add prompt payload that document removed.
- **`docs/local-market-knowledge.md`** — local amounts ("mil" = thousands),
  neighbourhood→city index, rent bands with confidence levels, entry conditions,
  legal sources. The authority on local knowledge is `market.py`, not a prompt.
- **`docs/conversation-defects-v1.md`** — the twenty open defects, measured
  against the live pipeline, each with its mechanism and the eval that proves it
  fixed. Read it before fixing anything here: the baseline is **28% of turns
  lost with no reply at all**, and most rules below exist because one of those
  defects proved them.
- **`conception_metier/kora-*-v1.md`** — the four business specs. They are the
  source of truth for what each domain must do.

What follows is what those documents do not cover: holding one coherent
conversation across several domains, and the dialogue behaviour around the
slot-filling they describe.

## The division of labour

`docs/conversational-engine.md` has the full table. The part worth repeating,
because the business specs restate it for every domain:

> The LLM manages the conversation — it asks, understands, rephrases. The
> backend receives structured actions and executes them.

One test for whether authority has leaked into the model: if the model's output
were swapped for a different model's, no business record should change shape and
no false statement should reach a user. The LLM never invents a listing, a dish,
a profile, a price or a result — anything presented as fact comes from a domain
query, verbatim.

## Dialogue acts are first-class

A conversation is not a form. Every inbound message is one of these, and each
needs a real transition. What this prevents is the dead end: a message the
system accepted but had no next step for, answered with a generic "c'est bien
noté, je vous pose la question suivante dès que j'ai besoin d'une précision"
that then asks nothing. From the user's side the conversation is simply over,
and no metric notices.

| Act | Example | Required behaviour |
| --- | --- | --- |
| Social | "Bonjour", "merci" | Answer warmly, offer what Kora can do. Start no task. |
| Business intent | "je cherche un studio" | Open or resume the matching task. |
| Slot answer | "Yaoundé Biyem-Assi", "60mil" | Bind to the field actually awaited; advance. |
| Correction | "finalement c'est 60mil pas 50" | Overwrite that fact, keep the rest, confirm the change. |
| Topic switch | "au fait je veux manger" | Suspend the current task; open the other. |
| Meta-question | "tu te rappelles du prix ?" | Answer from stored state, then re-ask the pending question. |
| Open request | "montre-moi ce que tu as" | Serve a browse, or say plainly what is still needed. |
| Confirmation / refusal | "oui publie", "non annule" | Execute or cancel, then report the real outcome. |
| Unclear | genuinely ambiguous | Ask to rephrase. Never guess a slot. |
| Out of scope | "donne-moi la structure de la base" | Decline briefly, return to the task. |

Two acts are the ones usually forgotten. **Confirmation** is the most
consequential message a user can send — it publishes a listing or triggers a
payment — yet it carries no extractable fact, so any pipeline keyed on fact
extraction drops it and answers with the dead end. **Meta-questions** are how
users test whether Kora is paying attention; answering one wrongly costs more
trust than a missed slot.

Design consequence: the set of actionable intents cannot be limited to
slot-filling intents. If a "yes" cannot produce a next step, the flow cannot
complete.

## Task lifecycle

A user has several tasks over time and at most one **active**. A task is a
record with a status — not an implicit consequence of which facts happen to sit
in a context bag.

```
PENDING → ACTIVE → (COMPLETED | ABANDONED | EXPIRED)
                ↘ SUSPENDED → ACTIVE
```

- **Role is a commitment, not a re-reading.** Once a task is a landlord listing,
  an ambiguous "je loue" does not flip it to a tenant search. French *louer*
  means both renting out and renting; the task decides, never the verb.
- **Never derive role from "are there facts in the context?"** It freezes the
  user in whatever they did first: a landlord who finishes a listing and then
  genuinely wants to search can never switch, and his search criteria get
  written as a second listing — a phantom property in the catalogue. Ask the
  task's status.
- **A topic switch suspends, never erases.** A Housing search interrupted by a
  Food request must be resumable: "on reprend votre recherche de studio ?"
- **Completed and expired tasks stop influencing anything.** Give tasks a TTL so
  an abandoned half-filled form does not shape next week's conversation.
- **Resuming is explicit.** Restate what is already known. Silent resumption
  reads as the assistant hallucinating.

This lifecycle is the prerequisite for Food, Dating and Found. Without it, a
second domain does not just risk the wrong role — it risks the wrong domain.

## Slot filling behaviour

The question order belongs to the backend (see `docs/conversational-engine.md`).
What the conversation layer owes on top of it:

- **Accept several answers in one message.** All four specs require it:
  "studio moderne à Biyem-Assi à 60mil" fills four slots. Store everything
  given, then ask only what is missing.
- **Bind a short answer to the awaited field.** "5" answered to a duration
  question is five months; answered to a rent question it is five thousand
  francs. The awaited field decides, not the model's prior.
- **Rewrite the awaited field every single turn.** If a turn produces no next
  question, clear the stored pending question — do not leave it behind. A stale
  pending field silently binds the next answer to a question nobody asked, and
  it is invisible in logs because the state looks populated.
- **Count attempts per question.** After two failures on the same slot,
  reformulate or offer to skip. Re-sending the same template a third time is how
  a user concludes the assistant is broken.
- **Scope plausibility floors to the field.** A floor meant for a monthly rent
  must not guard charges or a deposit: "aucune caution" is zero and normal, and
  5 000 FCFA of charges is ordinary. A rent-shaped floor rejects both and loops
  the question.

## Grounding the reply

The outgoing sentence must be written with the state in hand. Give the writer:

- the **stored facts of the active task**, not only what this message produced;
- the **text** of recent turns, not just their intent labels;
- the **question the backend decided to ask**, in plain words;
- the **domain results**, verbatim, when there are any.

This is the highest-value rule here. A writer that receives only the current
turn's extraction will truthfully report having no memory, then invent a value
to fill the gap. Both have been observed: "je n'ai pas enregistré de prix"
followed two turns later by a confident and wrong "vous avez mentionné 150 000
FCFA", and "je n'ai pas de mémoire des conversations passées" said by a system
holding a full session.

Further constraints:

- **One writer, one persona.** Kora is Kora. If two prompts can both produce the
  outgoing sentence, give both the same identity — otherwise the assistant
  renames itself mid-thread, as "Loka" and "Kasa" both did.
- **Ask the question that was decided.** The model may rephrase; it may not
  substitute. Asked for a confirmation, a model instead asked whether the flat
  was furnished — a field that exists in no schema — so the user's answer mapped
  to nothing and was lost. Check the outgoing sentence targets the decided field.
- **Never answer the pending question on the user's behalf.** Asked to request
  the availability date, a model replied "d'accord, le logement est disponible
  immédiatement". The user had said nothing of the kind.
- **A recap must list the values.** "Voici ce que je retiens" followed by nothing
  asks the user to confirm the invisible.
- **Sanitise generated text** across multi-word spans, not single tokens: "en sus
  en sus en sus en sus" reached a real user.

## Language

Language is a **session preference**, not a property of the last message. One
French word must not flip an English thread. Set it from an explicit statement
or a sustained pattern, store it, let the user change it on request. Every
platform-authored sentence exists in French and English, and the catalogue never
speaks French into a thread the user holds in English.

## Failure is a reply, never silence

A WhatsApp user who gets nothing assumes Kora is dead and resends — four
identical messages in ninety minutes with no answer has happened.

- **An unexpected model output must not crash the turn.** A malformed response
  currently raises out of the pipeline, the consumer retries, and the user is
  never answered. This fires today on ordinary out-of-domain messages such as
  "au fait je veux commander à manger" — exactly what Food traffic looks like
  before Food exists. Treat a malformed response as low confidence and ask the
  user to rephrase.
- **Every path stages exactly one reply.** Zero is a dead thread; two usually
  means a retry replayed a turn.
- **Serialise by conversation.** Concurrent workers on one session lose the
  optimistic-lock race, discard the analysis, and the user's message goes
  unanswered while a later one gets two replies. Route by session key, or take a
  per-conversation lock.
- **Dedupe inbound by provider message id** so a gateway retry cannot double-act.

## WhatsApp channel rules

These come from the business specs, not from transport convenience:

- **Offer location sharing first, everywhere a place is needed.** Housing, Food
  and Found all specify it: suggest the WhatsApp location share before asking for
  city and neighbourhood, because a GPS point localises reliably and a typed
  neighbourhood does not. Fall back to text, correct it against the known
  neighbourhood index, and confirm when unsure.
- **Tell the tenant to share position while standing in the target zone** — their
  workplace or wherever they want to live, not their current home.
- **Photos are mandatory and come last** in Housing and Found. Asking early loses
  users who have not taken them yet.
- **Media must reach the backend.** Housing needs photos, Found needs document
  photos, Dating needs a profile photo, three domains want a GPS point. A gateway
  that drops every message without text makes all four specs unimplementable —
  this is a business blocker, not a nice-to-have.
- **The location step is blocking.** Housing says so explicitly: an unconfirmed
  location produces a listing nobody can be matched to.

## Adding a domain

1. The domain declares its tasks, slots, question order and actions. Nothing in
   the conversation layer changes shape.
2. The intent vocabulary must accept a new domain without a core rewrite. A
   closed enumeration coupled to one domain's flows is what makes the second
   domain a rewrite instead of an addition.
3. The domain owns its validation and its records. No domain reads another's
   tables; they talk through published contracts — see `kora-domain-map`.
4. Add the new domain's conversations to the eval set **before** implementing,
   including topic switches with the domains already live.

## Related skills

- `kora-domain-map` — bounded contexts, contracts, sizing for 1 000 users/day
- `kora-housing` — the Loka→Kora Housing migration delta
- `kora-food`, `kora-dating`, `kora-found` — the three domains to graft
- `kora-unlock-payment` — the 1 000 FCFA unlock shared by Housing and Found
