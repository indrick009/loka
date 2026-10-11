---
name: kora-domain-map
description: >-
  Kora's bounded contexts, their boundaries, the contracts between them, and the
  sizing that 1 000 users/day actually requires. Use this skill before creating
  or moving any module, package or table; before letting one domain read
  another's data; when deciding where a new business rule belongs; when mapping
  the existing Loka contexts onto Kora's four domains; and whenever someone
  proposes microservices, Kubernetes, a shared "core" module, or a new
  abstraction layer. Use it too when judging whether the platform can carry its
  load, or when a change would couple Housing, Food, Dating or Found to each
  other. Read it before any structural refactor, because the cost of a wrong
  boundary is paid again on every later domain.
metadata:
  origin: Kora
  scope: cross-cutting
---

# Kora Domain Map

Kora is one conversational product over four independent businesses. The
architecture has exactly one job: let Housing, Food, Dating and Found evolve
separately while the user experiences a single conversation.

## The contexts

Loka already ships nine bounded contexts with domain/application/infrastructure
layering, aggregates, value objects, repositories and an outbox. That structure
is sound and is the starting point, not something to replace.

| Context | Role | State today |
| --- | --- | --- |
| **Conversation** | The single entry point: sessions, turns, tasks, dialogue acts, intent understanding, outbound replies. Today split across `ai` + `messaging`. | Exists; needs the task lifecycle from `kora-conversation-engine` |
| **Identity** | Who the user is. A WhatsApp number proves possession, so first contact auto-registers. Shared by all domains. | Exists |
| **Housing** | Listings, search, matching, one round of negotiation, availability. Today `property`, plus the landlord *directory*. | Exists; see `kora-housing` for the delta |
| ~~`rental`~~ | Rental applications, accept/refuse, unlimited counter-offers with history. **Out of scope, to delete** (11 Oct): §8 wants one counter-offer then stop, and no application at all. **Blocked, and not mechanical:** the payment is keyed on a rental application — `ServiceFeePayment.application_id`, `AccessGrant.application_id` and their uniqueness constraints — so deleting this context would leave the whole revenue path anchored on a concept that no longer exists. Re-anchor the payment first. | 1 159 lines, wired |
| ~~`visit`~~ | Kora scheduling, cancelling and completing visits. **Out of scope, to delete** (11 Oct): the two parties arrange it themselves once the contact is released. | 763 lines, wired, awaiting removal |
| `landlord` | **Directory only** — who owns this listing. Publication depends on it, so it stays. The ~1 400-line verification flow no longer gates anything; `is_verified` is a ranking signal the search already filters on, and a fraud-engine input. | Gate removed 11 Oct |
| **Food** | Restaurants, recurring menus, menu of the day, dish search, reservation requests. | To build — `kora-food` |
| **Dating** | Profiles, preferences, discovery, matches. | To build — `kora-dating` |
| **Found** | Lost/found declarations, ownership proof, controlled restitution. Priority 2. | To build — `kora-found` |
| **Payment** | Two models, different shapes: the 1 000 FCFA contact unlock (Housing, Dating, Found) and the restaurant's 2 000 FCFA weekly subscription (Food). | Exists, but `mock_gateway.py` only — no Fapshi, and no subscription model at all |
| **Trust & Safety** | Fraud, abuse, minors, AI-generated photos, fishing for names. Today `fraud`. | Exists; scope widens sharply with Dating and Found |
| **Reviews & Complaints** | A complaint names the provider to sanction or ban; a review decides which offers Kora puts forward. Shared by all four domains. | To specify — `conception_metier/kora-avis-plaintes-v1.md` |

Four business domains, five shared contexts. That is the whole map. Resist
adding a sixth: a context that exists "to hold common things" becomes the place
every domain quietly couples through.

Reviews & Complaints is the newest, and it is genuinely shared rather than a
catch-all: the thing sanctioned is a **provider** — a landlord, a restaurateur,
a finder, a dating profile — and a provider is not owned by any one domain.
Sanctioning an offer instead would let a banned landlord republish under a new
listing.

## The dependency rule

```
          ┌──────────────────┐
          │   Conversation   │  knows every domain through ports
          └────────┬─────────┘
                   │  ports declared by Conversation,
                   │  adapters implemented per domain
   ┌───────┬───────┼───────┬────────┐
   ▼       ▼       ▼       ▼        ▼
Housing  Food   Dating  Found   Payment / Identity / Trust&Safety
   └───────┴───────┴───────┴────────┘
          no domain imports another
```

Three invariants, each load-bearing:

1. **Conversation depends on domains; domains never depend on Conversation.** A
   domain that imports a session or an intent cannot be tested, reused or
   replaced, and the single entry point stops being a seam.
2. **No domain reads another domain's tables or repositories.** They talk through
   published contracts. Loka already does this correctly where it counts: the AI
   context reaches the catalogue through a `PropertySearchGateway` port, not the
   property repository. Keep that discipline for every pair.
3. **The port is declared by the consumer, implemented by the provider.** That is
   what keeps the dependency pointing the right way, and what lets a new domain
   be built without editing the conversation core.

Verify the real dependency graph; do not trust folder names. A directory called
`domain` that imports SQLAlchemy is infrastructure wearing a costume.

## Where a rule belongs

Ask these in order; the first "yes" wins:

1. Would this rule still hold with no WhatsApp and no LLM? → the **domain**.
2. Does it coordinate several aggregates or an external call? → **application**
   (a use case).
3. Is it about how something is said, asked or parsed? → **Conversation**.
4. Is it about a database, an HTTP client, a queue? → **infrastructure**.

A rule that cannot be placed usually means a boundary is wrong, not that a new
layer is missing.

## Contracts between contexts

Keep these few and explicit. Each is a published interface with a stable shape,
changed by expand-then-contract:

| Consumer → Provider | Contract |
| --- | --- |
| Conversation → Housing | search the catalogue; register a listing from confirmed facts; ask a landlord to confirm availability |
| Conversation → Food | publish a menu of the day; search today's dishes; send a reservation request |
| Conversation → Dating | create/update a profile; propose matches; record a like |
| Conversation → Found | declare a found document; open a claim; submit ownership evidence |
| Housing / Found → Payment | initiate the 1 000 FCFA unlock; learn when it settled |
| Any domain → Identity | resolve the user behind this phone number |
| Any domain → Trust & Safety | screen content; raise a signal; read a risk verdict |

A result crosses a boundary as plain data the conversation can print verbatim —
never as an aggregate, and never as a row the consumer re-interprets.

## Sizing for 1 000 users/day

Working from the configured limits rather than from guesses:

- **Inbound volume.** At ~10 messages per active user, ~10 000 inbound
  messages/day. Concentrated in a few hours, a plausible peak is ~2 000
  messages/hour ≈ **0.6 messages/second**, with short bursts above it.
- **Outbound.** `WHATSAPP__MAX_OUTBOUND_PER_MINUTE=120` allows 172 800/day —
  about seventeen times what 1 000 users need. Not a constraint.
- **Model calls.** The deterministic fast lane answers greetings, bare yes/no and
  bare amounts at zero tokens. If it absorbs ~30%, that leaves ~7 000 model
  calls/day. Against the configured `AI__DAILY_BUDGET_USD=25` the ceiling is
  about **$0.0036 per call** — comfortable for a Flash-class model, and the
  reason the token-reduction work in `docs/conversational-engine.md` matters: it
  is what preserves this headroom.
- **Per-user guard.** `AI__PER_USER_DAILY_BUDGET_USD=0.25` caps one abusive
  conversation without throttling everyone.

### What the current stack already covers

One PostgreSQL with pgbouncer, one Redis, one RabbitMQ, one API process, one or
two workers — all already in `docker-compose.yml`. At 0.6 messages/second this is
nowhere near its limits. **No Kubernetes, no microservices, no sharding, no read
replicas, no vector database.** Proposing any of them at this scale needs a
measurement first, and none exists that would support it.

### The three real constraints at this scale

Scale is not the risk. These are:

1. **The single Baileys session is a single point of failure.** One socket, one
   number, auth state on a local volume, an in-memory LID→phone map lost on
   restart. It cannot be replicated or rescheduled without re-pairing.
   Availability here matters far more than throughput anywhere.
2. **Per-conversation serialisation.** Workers run concurrently with no ordering
   per session, so two messages arriving together race on the optimistic lock and
   a reply is lost or duplicated. A correctness bug that worsens with traffic,
   not a capacity problem.
3. **Model latency inside the turn.** The user is waiting on WhatsApp. Keep the
   model call outside every database transaction — already the case — and keep
   the prompt small, which is the same discipline that protects cost.

### What to watch, and what would change the design

Instrument these before adding anything: inbound rate, replies staged per inbound
(must be exactly one), model latency p95, daily model spend, DLQ depth,
optimistic-lock conflicts per hour, gateway reconnects.

Revisit the architecture when one of these actually happens: sustained traffic
above ~5 messages/second; a need for several WhatsApp numbers; a domain whose
write volume dwarfs the others; or an availability target the single gateway
cannot meet.

## Migration order

Housing first, because it exists and it is where the paying users are. The order
below is driven by **launching Housing**, not by preparing four domains — that
distinction changes the sequence, so do not reorder it from domain priority
alone. The defect ids refer to `docs/conversation-defects-v1.md`.

1. **Repair the conversational core** — the lost-turn crash (D1) and
   per-conversation serialisation (D2) first, because they hit real users today
   and are small; then actionable dialogue acts, clean pending state and a
   grounded reply writer (D4, D5, D7, D8). See `kora-conversation-engine`.
2. **The media and location pipeline** (D3) — photos are a mandatory step of the
   Housing spec, so without it Housing cannot publish at all.
3. **Revenue** — the availability check before any charge (D10), then the real
   Fapshi adapter (D11). See `kora-unlock-payment`.
4. **Housing → Kora Housing** — the business delta: quantity, water and
   electricity, deposit in months, the security step, and the tenant's months of
   advance. See `kora-housing`.
5. **Conversation task lifecycle** — the prerequisite for a **second** domain,
   not for launching the first. See `kora-conversation-engine`.
6. **Food**, then **Dating** — each a new context, no change to the core.
7. **Found** — priority 2, gated on Trust & Safety work.

An earlier revision of this skill put the task lifecycle first. That is the right
order for readying four domains and the wrong order for shipping Housing, and it
omitted both the core repairs and the media pipeline entirely.

Record each structural decision as an ADR so the next domain inherits the
reasoning instead of re-litigating it.

## Related skills

- `kora-conversation-engine` — the conversational contract every domain shares
- `kora-housing`, `kora-food`, `kora-dating`, `kora-found` — per-domain rules
- `kora-unlock-payment` — the 1 000 FCFA unlock invariants
- `docs/conversational-engine.md`, `docs/local-market-knowledge.md` — the existing baseline
