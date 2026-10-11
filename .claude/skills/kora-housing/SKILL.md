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
| `minimum_duration` and `conditions` | **Dropped** — decided 11 Oct. Kora connects people, it does not negotiate leases. The quality gate has to stop requiring them. |
| `advance` in months | **New.** Not in the nine steps, but §8 negotiates on it and the tenant is asked for theirs, so the landlord's requirement has to exist. |
| `availability` | **Still open** — the owner and §9.1 disagree. See Still open. |

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

## Settled business decisions (11 October 2026)

**Money is counted in months of rent, not in francs**, and the caution is
**added on top of** the advance — it is not an alternative to it:

> "tu donnes une avance en plus du nombre de mois que tu payes, et le jour que
> tu veux partir on peut te rembourser cela"
> "je paye 6 mois de loyer et j'ajoute deux mois comme caution remboursable"

| Field | Meaning | Refundable |
| --- | --- | --- |
| `advance` | months of rent paid up front | no |
| `deposit` (caution) | **extra** months on top of the advance | yes, on departure |

So the entry cost is `(advance + deposit) × rent`. In the example: six months
advance plus two months caution, eight months of rent handed over, two of them
coming back. Modelling the caution as a parallel or alternative amount gets the
tenant's real cost wrong, which is the number the matching has to compare.

Store the months. The franc figure is derived from the rent, so correcting a
rent cannot leave a stale total behind, and the tenant's "how many months can
you pay" compares directly against the landlord's requirement with no
conversion. A deposit held as an XAF amount silently decouples from the rent it
guarantees.

**The caution is the landlord's requirement, not Kora's.** Kora records what the
landlord asks for and shows it to the tenant. It never sets it, never holds the
money, and never arbitrates the refund — that happens between the two parties
long after Kora is out of the picture (see below), so Kora must promise nothing
about it.

**Kora's involvement ends when the contact is released.**

> "une fois les informations décaissées Kora a fini son travail, plus aucun
> argent ne vient à Kora"

One payment of 1 000 FCFA, then out. No rent collection, no commission on the
lease, no tenancy management, no deposit escrow, no follow-up. This is the
hardest boundary in the domain and the easiest to erode feature by feature: any
proposal that keeps Kora in the relationship after disclosure is outside V1.

**No entry-conditions step.** Kora puts a landlord and a tenant in touch; it is
not a lease negotiator. Drop the free-text `conditions` collection (agency fees,
accepted tenant type) and the `minimum_duration` step with it. `Property.
blocking_issues()` must be relaxed accordingly, or a listing that followed the
spec still cannot publish.

Keep the advance and the deposit: they are not "conditions", they are the price
of entry, and §8 negotiates on exactly those two axes.

**Availability is the landlord's call, always.** Confirmed 11 Oct. Kora never
infers it — not from a match, not from a payment, not from elapsed time. A
listing stays available **by default** until the landlord says otherwise.

Note what the message asks, because it is not a payment request:

> « Le logement est encore disponible ? Un locataire aimerait visiter. »

| Landlord's answer | Effect |
| --- | --- |
| yes | the tenant may pay the 1 000 FCFA |
| "un studio a été pris" | quantity drops by one; the listing stays if any remain |
| "tout a été pris" | the listing leaves the catalogue |
| no answer | **not confirmed** — no payment; offer other matches instead |

What is *not* wanted is availability **management**: no state machine tracking a
listing over time, no periodic re-checks, no expiry sweep. One question, at the
moment it matters, because the state may have changed since publication.

**Quantity decrements only on the landlord's declaration** — never on a match, a
confirmation or a payment. That follows from the boundary above: once the contact
is released Kora is out of the relationship and cannot know whether a lease
happened. Only the landlord knows. With `quantity = 2`, "is it still available?"
means "are any left?", which is exactly why the two declarations above are
different events.

## Still open — do not resolve by writing code

**The landlord's required advance.** §8 negotiates on it — "le bailleur demande
un an d'avance et le locataire a 5 mois" — but none of the nine collection steps
asks for it. Either it becomes a step, or negotiation covers the price only.
Settle it before implementing §8.

## Scope

§10 is explicit: this is V1 and **nothing else is to be added on the business
side**. No tenancy management, no rent collection, no contracts, no visit fees.
When tempted, re-read that sentence.

## Related skills

- `kora-conversation-engine` — task lifecycle, role commitment, channel rules
- `kora-unlock-payment` — the 1 000 FCFA unlock and its preconditions
- `kora-domain-map` — Housing's boundary and its contracts
- `docs/conversational-engine.md`, `docs/local-market-knowledge.md` — current baseline
