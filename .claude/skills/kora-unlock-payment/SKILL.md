---
name: kora-unlock-payment
description: >-
  The 1 000 FCFA unlock that is Kora's entire revenue model — what the user pays
  for, what must be true before they are ever asked, and what is revealed after.
  Use this skill whenever you touch payments, the Fapshi integration, the
  service fee, contact or location disclosure, availability checks before a
  charge, payment callbacks, or refunds. Use it too when adding a paid step to
  any domain, or when deciding what a user receives in exchange for paying.
  Read it before writing any code that charges a user or reveals a counterparty,
  because charging for something unavailable, or revealing a contact before
  payment settles, are the two failures that would destroy trust in Kora fastest.
metadata:
  origin: Kora
  scope: cross-cutting
---

# Kora Unlock Payment

Kora replaces the estate agent. An agent charges a 5 000 FCFA viewing fee and
then a full month's rent in commission. **Kora charges 1 000 FCFA, once, to the
person who receives the contact.** That number is the product.

Shared today by Housing (`conception_metier/kora-housing-v1.md` §9) and Found
(`kora-found-v1.md` §8). Dating and Food have no paid step in V1.

## What the money buys

| Domain | Payer | Unlocked |
| --- | --- | --- |
| Housing | the tenant | the landlord's phone number **and** the property's location |
| Found | the document's owner | contact with the finder |

Nothing else is sold. No viewing fee, no commission, no subscription, and no
charge to the landlord, the restaurant or the finder.

## State today

- `initiate_service_fee.py` already defaults to `service_fee_xaf: int = 1000`,
  with a `ServiceFeePayment` aggregate, a callback handler carrying signature
  verification and an idempotency key. The domain model is in place.
- `payment/infrastructure/gateways/` contains **only `mock_gateway.py`**. There
  is no Fapshi adapter — no real payment has ever been taken.
- `AVAILABILITY_CHECK` exists as a flow name and as intents
  (`AVAILABILITY_STILL_AVAILABLE`, `AVAILABILITY_RENTED`,
  `AVAILABILITY_TEMPORARILY_UNAVAILABLE`) but **no use case implements it**, so
  the blocking precondition below is currently unenforced.

## The two invariants that cannot bend

**1. Availability is confirmed before the user is asked to pay.**

Housing §9 opens with it: before any payment, Kora asks the landlord whether the
property is still available, and the tenant pays only if the landlord confirms.
This is the whole difference from the agent who takes a viewing fee for a flat
rented two weeks ago. A charge taken against a stale listing is a refund, a
complaint, and a user who never comes back.

Treat an unanswered availability check as **not** confirmed — silence is not a
yes. Give it a timeout, tell the tenant plainly that the landlord has not
replied, and offer other matches instead of holding them in limbo.

**2. Payment settles before anything is revealed.**

The number and the location are disclosed only once the provider confirms
settlement — never on the user's word, never optimistically. Equally, the charge
is never taken before the thing being bought is known to exist: Found §8 is
explicit that payment is requested **only after** ownership proof is validated.

## The payment conversation

Both specs describe the same sequence. Keep it; each step stops a specific
failure:

1. Kora asks "Voulez-vous procéder au paiement ?" — an explicit opt-in, so
   nobody is charged by momentum.
2. Kora proposes a known number, and **the user can change it.** The WhatsApp
   number is not always the mobile-money number.
3. On validation, Kora initiates the withdrawal request.
4. The user validates on their own phone. Kora never holds or asks for their PIN
   or credentials.
5. Settlement arrives by provider callback — the only event that authorises
   disclosure.

Alongside: confirm the amount in words before initiating; make an abandoned or
failed payment resumable without redoing the whole flow; never charge twice for
the same unlock. The existing idempotency key enforces that last point — keep it
keyed on the thing being unlocked, not on the attempt.

## Fapshi

Both specs name Fapshi. When building the adapter:

- It is an **adapter behind the existing port**, like `mock_gateway.py`. The
  domain must never learn the word Fapshi; changing provider must not reach the
  aggregate.
- **Verify the callback signature** and treat the callback as untrusted input. An
  unverified settlement callback is a free unlock for anyone who guesses the
  endpoint. The verification exists — never bypass it for convenience.
- **Callbacks are idempotent, may arrive out of order,** twice, or for a payment
  already settled. Make the handler safe under replay.
- **Reconcile.** A callback can be lost. Poll payments pending beyond a threshold
  rather than leaving a paid user un-served — someone who paid and received
  nothing is the worst outcome in the system.
- Keep credentials in configuration, never in code, and keep provider responses
  out of logs that pair amounts with phone numbers.

## Refunds and disputes

V1 has no automated refund. That is acceptable only because the two invariants
above make refunds rare. Still:

- Record enough to settle a dispute by hand: what was unlocked, when, for whom,
  the provider reference, and the availability confirmation that preceded it.
- If a tenant pays and the landlord proves unreachable or the property is gone,
  that is Kora's failure, not the tenant's. Decide the policy before launch, not
  during the first complaint.

## Adding a paid step to a new domain

Answer these before charging for anything:

1. What exactly does the user receive, in one sentence they would recognise?
2. What must be verified true before they are asked? (availability, ownership,
   consent)
3. What is revealed, and only after settlement?
4. What happens if they pay and the thing fails?

If any answer is unclear, the step is not ready to be paid.

## Related skills

- `kora-housing` — where the unlock sits in the tenant journey
- `kora-found` — ownership proof as the precondition to payment
- `kora-conversation-engine` — confirmation as a first-class dialogue act
- `kora-domain-map` — Payment as a shared context behind a port
