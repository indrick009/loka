---
name: kora-food
description: >-
  Kora Food V1 — restaurant registration, recurring menus, the menu of the day
  as the only source of truth, client search by proximity or by dish, and the
  reservation request sent to the restaurateur. Use this skill whenever you
  touch restaurants, menus, dishes, food search, food orders or reservations, or
  when building the Food bounded context from scratch. Use it before modelling
  anything about dish availability, because the menu of the day — not a standing
  catalogue — is what makes this domain work, and a dish offered outside one is
  a promise Kora cannot keep. Read it when grafting Food onto the existing
  conversation, since Food traffic is what currently crashes the pipeline.
metadata:
  origin: Kora
  scope: domain
---

# Kora Food

Source of truth: **`conception_metier/kora-food-v1.md`**. This skill carries the
engineering consequences.

The problem: much of the food in Cameroon is sold by neighbourhood restaurants
and vendors whose menu changes day to day. To know what is available near you
today you have to walk there or call. Kora lets the restaurateur publish today's
menu and the client find a dish available today, nearby or to their taste.

Nothing exists in the code yet. This is a new bounded context.

## The rule that shapes the whole domain

> **"C'est le menu du jour qui compte pour Kora."**
> **"Kora ne propose jamais un plat qui ne figure pas dans un menu du jour publié."**

Availability is not a property of a dish; it is a **daily publication**. Model it
that way and the domain stays simple:

- A `Restaurant` is registered once.
- A `RecurringMenu` is a reusable template, never something a client can be
  matched against.
- A `DailyMenu` is the only thing search reads. It is dated, it belongs to one
  restaurant, and it expires.

Getting this wrong produces exactly the failure the product exists to fix:
sending someone to a restaurant for a dish that is not cooking today. A standing
dish catalogue with an `available` boolean is the wrong model — staleness becomes
invisible, and the default drifts toward "available".

Decide explicitly: when does a daily menu stop being searchable — at a fixed
hour, at midnight, or after a window? There is no correct answer in the spec;
pick one, write it down, and make it visible to the restaurateur.

## Restaurateur flow

**Registration** — two fields only:

| # | Information | Notes |
| --- | --- | --- |
| 1 | Restaurant name | |
| 2 | Location | Offer the WhatsApp location share **first**, as in Housing. Otherwise city + neighbourhood, corrected against the known index, confirmed when unsure. |

**Recurring menus** — the dishes that come back often during the week. They exist
to make composing today's menu fast, nothing more.

**Menu of the day:**

| # | Information | Notes |
| --- | --- | --- |
| 1 | Dishes available today | Written freely, or picked from the recurring menus |
| 2 | Price of each dish | Local amounts: "2 mil" = 2 000 FCFA |

Once submitted, the menu becomes visible to client searches.

Two things make this flow pleasant or painful, and they are worth the effort:
publishing from recurring menus must take one short message, because a
restaurateur does this every single day; and the amount convention is already
solved — reuse `market.py` rather than re-deriving it in a prompt.

## Local dish names

Kora must understand local dish names: ndolè, poulet DG, soya, poisson braisé,
and the rest. This belongs in the market knowledge base next to the cities and
neighbourhoods — code, tested, with the same confidence levels — not in a prompt.
`docs/local-market-knowledge.md` and `market.py` cover amounts and places today;
dishes are the extension Food needs. A dish index also gives search its synonyms
and spelling tolerance, which is the same problem the neighbourhood index already
solves.

## Client flow

Two ways to search, possibly combined:

| Kind | Example | What Kora uses |
| --- | --- | --- |
| By proximity | "Je cherche la nourriture auprès de moi" | The client's position — advise the WhatsApp location share, else city + neighbourhood |
| By dish | "Je cherche du poulet braisé" | The dish or kind of dish |

Keep the question count minimal, as in Housing: a hungry person will not fill in
a form. If a proximity search has a position and a dish, that is enough to
answer.

## Matching

Kora searches **the published menus of the day** for dishes matching the request
— proximity, kind of food, or both.

- Results are the catalogue speaking: print the restaurant, the dish and the
  price verbatim. A price a model rephrased is a dish that does not exist.
- Empty results are a real answer. Say so plainly and offer the move that
  changes the outcome — a wider area, or a different dish.

## Reservation request

Based on the match, Kora **sends the request to the restaurant owner** to reserve
the client's dish.

Note what V1 is not: no payment, no delivery, no order basket, no order tracking.
A request goes to the restaurateur; that is the extent of it. Resist adding the
rest — `conception_metier/kora-food-v1.md` §8 closes the scope.

Still, two things need a decision before building: what happens when the
restaurateur does not answer, and whether the client is told their request was
received or accepted. Those are different promises.

## Grafting Food onto the conversation

Food is the first new domain, so it is the test of the conversation engine.

- A Food message arriving today **crashes the analysis pipeline** — "au fait je
  veux commander à manger" raises a malformed-response error, the consumer
  retries, and the user is never answered. Fix that before Food exists, because
  it is already happening with real users.
- Topic switches between Housing and Food must preserve both tasks. See
  `kora-conversation-engine` on task lifecycle.
- Add Housing↔Food switch conversations to the eval set **before** implementing.

## Related skills

- `kora-conversation-engine` — task lifecycle, channel rules, dialogue acts
- `kora-domain-map` — Food's boundary and its contract with Conversation
- `kora-housing` — the location and amount conventions Food reuses
- `docs/local-market-knowledge.md` — where dish names belong
