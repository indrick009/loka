---
name: kora-found
description: >-
  Kora Found V1 — the finder's declaration, the owner's claim, ownership proof
  before any disclosure, human validation, and controlled restitution. Priority
  2. Use this skill whenever you touch lost-and-found declarations, document
  matching, ownership verification, identity-document photos, or the restitution
  flow. Use it before modelling the Found bounded context, and before writing
  any code that compares or reveals information taken from an identity document.
  Read it in full before implementing anything, because a wrong disclosure here
  hands someone else's national ID card to a fraudster, and most of the rules
  below exist to make that impossible rather than merely unlikely.
metadata:
  origin: Kora
  scope: domain
---

# Kora Found

Source of truth: **`conception_metier/kora-found-v1.md`**. This skill carries the
engineering consequences. Note that the spec marks its `[Sécurité]` rules as
**proposals awaiting validation** — confirm them with the product owner before
treating them as settled.

**Priority 2.** §11 is explicit: Found must not delay Housing, Food or Dating.
Do not start building it while those are in flight.

Nothing exists in the code yet.

## The problem and the shape of the solution

Losing a national ID card (CNI) in Cameroon is a serious ordeal. Someone who
finds a document has no way to reach its owner; the owner has nowhere to look.
Kora connects the **finder** and the **owner** — but only after the owner has
**proved the document is theirs**.

Documents covered: CNI, passport, driving licence, student card, other documents
and objects.

## The asymmetry that defines this domain

Every other Kora domain matches two people who both want to be found. Found does
not. Here, one side holds something valuable and the other side must prove a
claim — and a plausible impostor is indistinguishable from the owner unless the
system is built to tell them apart.

That asymmetry produces one rule from which almost everything else follows:

> **Kora never confirms to a claimant that a document in a specific person's name
> has been found, until ownership proof is validated.**

It may only say that a possible match exists, and ask for proof. The reason is
concrete: without it, anyone can type names to learn which documents have been
found, which is both a privacy breach and the first step of an impersonation.

## Finder flow

| # | Information | Notes |
| --- | --- | --- |
| 1 | Document type | CNI, passport, licence, student card, other |
| 2 | Where it was found | WhatsApp location share, or city + neighbourhood, verified as in Housing |
| 3 | Date found | |
| 4 | Full description | All the detail the finder can give |
| 5 | Photos | Photos of the document |

**[Sécurité]** Before the photos are sent, Kora explains to the finder that they
will **never be published** nor shown to people searching — they serve only for
verification. Say it before the upload, not after.

## Owner flow

| # | Information |
| --- | --- |
| 1 | Document type |
| 2 | Full name appearing on the document |
| 3 | Approximate place and date of loss |
| 4 | Description |
| 5 | Proof of ownership |

## Matching

The backend compares finders' declarations with owners' claims on type, name,
place, date and description.

A match is a *candidate*, never an announcement. The claimant learns that a
possible match exists; they learn nothing about the document until proof is
validated.

## Ownership verification

Acceptable evidence includes a photocopy or old photo of the document, the loss
declaration from the police or gendarmerie, the CNI application receipt, another
document in their name, or information printed on the document that only the
owner would know (document number, date and place of birth).

**[Sécurité]** — the three rules here are the heart of the domain:

1. **Kora compares the claimant's evidence against the finder's photos without
   ever showing those photos to the claimant.** The comparison happens inside the
   backend; the images never cross to the other party.
2. **The claimant must supply the information; Kora never suggests it.** No
   question of the form "Êtes-vous né le 12 mars ?" — that hands the answer to an
   impostor. Ask open questions and compare; never offer a value to confirm. This
   constrains the conversation layer directly: a model that helpfully paraphrases
   stored data will leak the answer, so verification prompts must receive the
   claimant's input and a verdict request, not the document's contents.
3. **In V1, final validation is performed by a member of the Kora team** before
   any connection is made. Build for a human in the loop: a review queue, a
   recorded decision, and an auditable reason.

A face photo of the claimant may be compared with the photo on the document.
Treat that as biometric comparison: sensitive, retained only as long as needed,
and never a sole criterion.

## Payment and connection

Only **after ownership proof is validated**, the owner pays **1 000 FCFA** to
unlock the connection with the finder. Never before. The payment sequence is the
one shared with Housing — see `kora-unlock-payment`.

## Restitution

**[Sécurité]**

- Restitution happens in a **public place**, or at a **police station or
  gendarmerie**. Kora recommends this explicitly to both parties.
- The finder may instead drop the document at the police station; Kora then tells
  the owner where to collect it.
- **No payment between finder and owner conditions the restitution.** The 1 000
  FCFA paid to Kora is the only fee. Any demand for money by the finder is
  flagged as a scam risk.

## Risks and the protections they require

The spec's §10 table is the requirements list, not commentary. Each risk maps to
something to build:

| Risk | What must exist |
| --- | --- |
| Impersonation — a fraudster claims someone's CNI | Mandatory proof, team validation, no document information revealed before validation |
| Fishing for names | No nominative confirmation before proof, **and a per-user search rate limit** |
| Ransom — the finder demands money | Restitution free between parties; reporting; account suspension |
| A stolen document declared as "found" | Finder's WhatsApp number retained; restitution via police recommended; reporting possible |
| Leak of document photos | Private encrypted storage, backend and review team access only, **no photo in any log**, deletion after restitution or after a set period |
| Assault at the meeting | Public place or police station recommended |
| Personal data | Collect only what is necessary, per loi n° 2024/017 on personal data protection |

Two of these are easy to forget because they are not features: the **search rate
limit** and the **retention deadline**. Both need a number, decided before launch.

## What to settle before building

- The retention period for finder photos and claimant evidence, and what deletion
  actually removes.
- Who on the team validates, how they are notified, and what their SLA is — a
  human in the loop with no response time is a queue that silently grows.
- Whether the `[Sécurité]` proposals in the spec are accepted as written. They are
  marked as awaiting your validation.

## Related skills

- `kora-unlock-payment` — proof before payment, payment before disclosure
- `kora-conversation-engine` — why the reply writer must not be handed the
  document's contents
- `kora-domain-map` — Found's boundary, and Trust & Safety as a shared context
- `kora-dating` — the other domain dominated by personal data
