---
name: kora-housing
description: >-
  Kora Housing V1 — the landlord listing flow, the four questions a tenant is
  asked, multiple-lodging rules, furnished daily rentals, matching, the
  negotiation protocol, and the availability check that gates payment. Also the
  explicit delta between what Loka implements today and what the Kora spec
  requires. Use this skill whenever you touch property listings, the catalogue,
  search, matching, landlords, tenants, visits, rentals, or the Housing
  conversational flow. Use it before changing PROPERTY_REQUIREMENTS,
  SEARCH_REQUIREMENTS, the property aggregate or its quality gate, and when
  migrating the Loka hébergement module to Kora logement. Read it before adding
  any Housing feature, because several rules here contradict what the code does
  today and the differences are deliberate.
metadata:
  origin: Kora
  scope: domain
---

# Kora Housing

Source of truth: **`conception_metier/kora-housing-v1.md`**. Read it. This skill
carries the engineering consequences and the migration delta, not a retelling.

The business in one line: renting in Cameroon goes through an agent who charges a
5 000 FCFA viewing fee and a month's rent in commission. Kora connects landlord
and tenant directly for **1 000 FCFA**, paid by the tenant. Kora is not a
property-management platform — keep the scope that narrow.

## Landlord flow: the order Kora requires

| # | Information | Notes |
| --- | --- | --- |
| 1 | Property type | room, studio, apartment, house |
| 2 | Modern or not | yes/no |
| 3 | **Several lodgings?** | see below — **new, absent from Loka** |
| 4 | **Location** | offer the WhatsApp location share **first**; fall back to city + neighbourhood, corrected against the known index, confirmed when unsure. **Blocking: must be validated before continuing.** |
| 5 | Rent | "60mil", "60k", "50000" all mean thousands — see `docs/local-market-knowledge.md` |
| 6 | **Electricity and water** | included in the rent or not. **Some lodgings have no water at all** — the question and the answer must make that case unambiguous. |
| 7 | **Deposit (caution)** | is there one, and **how many months** |
| 8 | **Security** | guard, parking. Subtlety: these usually exist from ~70 000 FCFA upward |
| 9 | **Photos** | **last step**, mandatory |

Then Kora publishes the lodging or lodgings.

### Delta against the code today

`docs/conversational-engine.md` documents the current order:
`property_type → standing → location → rent → charges → deposit →
minimum_duration_months → availability → conditions → CONFIRM`.

| Change | Nature |
| --- | --- |
| "Several lodgings?" as step 3 | **New.** Drives quantity and listing splitting. |
| Location offers WhatsApp share first, and is blocking | **New.** The gateway drops non-text messages today, so this is unimplementable until media reaches the backend. |
| `charges` becomes electricity and water, stated separately | **Change.** The schema already carries `water_charges` and `electricity_charges` — the flow does not use them, and "no water" is not representable as included/extra. |
| Deposit expressed in **months** | **Change.** Stored as an XAF amount today. Months is what landlords say; derive the amount from the rent. |
| Security (guard, parking) as a collection step | **Change.** `amenities` already has `parking` and `security` booleans; no step collects them. |
| Photos as the explicit last step | **Partially present.** The quality gate reports `MISSING_PHOTOS`, but there is no media pipeline. |
| `minimum_duration`, `availability`, `conditions` | **Open question — do not silently drop.** The spec's nine steps omit them, but `Property.blocking_issues()` requires them and the market knowledge calls entry conditions "half the price". See Open questions. |

### Several lodgings

| Situation | Treatment |
| --- | --- |
| Different types (2 studios + 1 room) | Collect the room's details separately from the studios' |
| Same type, same characteristics (2 identical studios) | **One listing with quantity = 2** |
| Same type, different prices (a studio at 60 000 and one at 70 000) | **Two distinct listings** |

Quantity does not exist in the model today. It is not a display detail: it
decides whether a match consumes availability or leaves some, and whether
"still available?" means "any left?" or "is it gone?".

### Furnished lodgings

A furnished room, studio or apartment is **generally rented by the day**. Most of
the process is unchanged — landlord flow, tenant flow, matching, availability
check, payment. What changes is the price unit. Neither furnishing nor a daily
unit exists in the code; a price that silently means per-day in a per-month field
is a listing that lies.

## Tenant flow: four questions, no more

| # | Information |
| --- | --- |
| 1 | Property type |
| 2 | Maximum rent they accept |
| 3 | **How many months of rent they can pay up front** |
| 4 | Search zone — advise sharing position **while standing in the target zone** (workplace or wherever they want to live), not at home |

> "C'est tout ce que Kora demande au locataire. Le reste, c'est au système de le
> déduire pour lui trouver un logement correspondant."

That sentence is a constraint on us, not a convenience for the tenant. Every
extra question is work the matching engine refused to do.

**Delta:** Loka asks three of these — type, location, budget. **Months of advance
is missing**, and it is not cosmetic: §8 makes the advance an explicit axis of
mismatch and negotiation. `standing`, `charges`, `deposit`, `availability` are
correctly never asked of a tenant, and must stay that way — the existing docs
already explain why: a question the tenant cannot answer is a thread that stops.

A defect to be aware of while migrating: in live testing, a tenant was asked for
charges, deposit, minimum duration and entry conditions, and a confirmation then
created a **listing out of their search criteria**. That is a phantom property in
the catalogue. The cause is role drift, not the question order — see
`kora-conversation-engine` on task lifecycle.

## Matching and negotiation

Two outcomes: a **direct match**, or a **gap** — the rent is 55 000 and the
tenant wants 50 000; or the landlord wants a year up front and the tenant has
five months; or both at once.

Kora may negotiate with the landlord **only when the listing gets little
demand**. The trigger is a demand signal per listing, which does not exist today
and must be modelled:

| Situation after publication | Decision |
| --- | --- |
| People interested at the asking price, nobody negotiating | The price is fine — **no negotiation** |
| Many people negotiating | Kora brings the landlord **the best offers**, without repeating itself |
| Nobody asking at all | Kora may approach the landlord |

Protocol, and the limit is the point:

1. Kora asks the landlord whether they could consider the condition.
2. If the landlord accepts, Kora confirms to the tenant.
3. If the landlord counter-offers, Kora passes it to the tenant **and stops
   there.** No repeated shuttling between the two parties.

Kora is a matchmaker with one round of negotiation, not a broker. "Without
repeating itself" is also an anti-spam rule: a landlord must not receive the same
offer twice, nor be contacted about a listing that is selling fine.

## Availability check before payment

Before any payment, Kora asks the landlord whether the lodging is still
available. The tenant pays only if the landlord confirms. An unanswered check is
**not** a confirmation.

`AVAILABILITY_CHECK` exists as a flow name, and the three answers exist as
intents (`AVAILABILITY_STILL_AVAILABLE`, `AVAILABILITY_RENTED`,
`AVAILABILITY_TEMPORARILY_UNAVAILABLE`), but **no use case implements it**. This
is the gate on revenue and the main thing that distinguishes Kora from the agent
who charges for a flat already rented.

Payment itself: see `kora-unlock-payment`. The tenant pays 1 000 FCFA and
receives the landlord's **number** and the lodging's **location**.

## Open questions to settle with the product owner

Do not resolve these by writing code:

1. **Minimum duration, availability date and entry conditions.** The spec's nine
   steps omit them; the property quality gate requires them; the market knowledge
   says entry conditions are half the real cost. Either the gate relaxes, or the
   spec gains a step. Both are defensible — this is a business call.
2. **The landlord's advance requirement.** The tenant is asked how many months
   they can pay, and §8 negotiates on it, so the landlord's expectation must be
   captured somewhere. It is not in the nine steps.
3. **Deposit in months or in francs**, and whether the stored value is the months
   or the derived amount.
4. **Quantity and matching.** Does a confirmed match decrement quantity, and at
   which point — on match, on availability confirmation, or on payment?

## Scope

§10 is explicit: this is V1 and **nothing else is to be added on the business
side**. No tenancy management, no rent collection, no contracts, no visit fees.
When tempted, re-read that sentence.

## Related skills

- `kora-conversation-engine` — task lifecycle, role commitment, channel rules
- `kora-unlock-payment` — the 1 000 FCFA unlock and its preconditions
- `kora-domain-map` — Housing's boundary and its contracts
- `docs/conversational-engine.md`, `docs/local-market-knowledge.md` — current baseline
