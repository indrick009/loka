"""What Found tells the conversation engine about itself."""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.conversation.application.plugs.forms import common_facts, text_of
from loka.bounded_contexts.conversation.application.plugs.spec import DomainSpec, FormField
from loka.bounded_contexts.conversation.domain.entities.conversation_session import FlowName
from loka.bounded_contexts.conversation.domain.services.extracted_facts import ExtractedFacts
from loka.bounded_contexts.found.domain.documents import DOCUMENT_TYPES

_TYPE = FormField(
    "document_type",
    "the kind of document: CNI, passeport, permis, carte d'étudiant or autre",
    {
        "fr": "De quel document s'agit-il ? CNI, passeport, permis de conduire, carte "
        "d'étudiant, ou autre ?",
        "en": "Which document is it? ID card, passport, driving licence, student card, "
        "or other?",
    },
)
_SHARE = "Partage la position WhatsApp, ou donne la ville et le quartier."

DECLARE_FORM = (
    _TYPE,
    FormField("place", "where it was found", {
        "fr": f"Où l'as-tu trouvé ? {_SHARE}",
        "en": "Where did you find it? Share the location, or give the city and district.",
    }),
    FormField("found_on", "when it was found", {
        "fr": "Quand l'as-tu trouvé ?", "en": "When did you find it?",
    }),
    FormField("description", "a full description, including the name written on it", {
        "fr": "Décris-le le plus complètement possible, avec le nom qui y figure.",
        "en": "Describe it as fully as you can, including the name written on it.",
    }),
    FormField("photo", "photos of the document", {
        "fr": "Envoie maintenant des photos du document. Elles ne seront jamais "
        "publiées ni montrées aux personnes qui cherchent : elles servent "
        "uniquement à vérifier qui en est le vrai propriétaire.",
        "en": "Now send photos of the document. They will never be published or "
        "shown to anyone searching: they are only used to verify the real owner.",
    }),
)

CLAIM_FORM = (
    _TYPE,
    FormField("full_name", "the full name written on the lost document", {
        "fr": "Quel est le nom complet écrit sur le document ?",
        "en": "What is the full name written on the document?",
    }),
    FormField("place", "where it was lost, approximately", {
        "fr": f"Où l'as-tu perdu, à peu près ? {_SHARE}",
        "en": "Where did you lose it, roughly? Share the location, or give the city.",
    }),
    FormField("lost_on", "when it was lost, approximately", {
        "fr": "Quand l'as-tu perdu, à peu près ?", "en": "When did you lose it, roughly?",
    }),
    FormField("description", "a description of the lost document", {
        "fr": "Décris-le : couleur, état, ce qu'il y avait avec…",
        "en": "Describe it: colour, condition, what was with it…",
    }),
)

RULES = (
    " Found: someone who found a document or an object is FOUND_DECLARE; "
    "someone who lost one is FOUND_CLAIM. 'document_type' is one of CNI, "
    "PASSEPORT, PERMIS, CARTE_ETUDIANT, AUTRE; the name on the lost document goes "
    "in 'full_name'; dates as said ('hier', '12 octobre') in 'found_on' or "
    "'lost_on'; 'description' in their own words. What an owner writes to prove "
    "the document is his (a document number, his birth date and place…) goes in "
    "'proof_text'. Never suggest a proof, never confirm a name or say a document "
    "was found."
)

ENTITIES: dict[str, Any] = {
    "document_type": {"type": "string", "enum": list(DOCUMENT_TYPES)},
    "full_name": {"type": "string"},
    "found_on": {"type": "string"},
    "lost_on": {"type": "string"},
    "description": {"type": "string"},
    "proof_text": {"type": "string"},
}


def validate(entities: dict[str, Any], flow: FlowName) -> ExtractedFacts:
    facts, rejected = common_facts(entities)
    if entities.get("document_type") in DOCUMENT_TYPES:
        facts["document_type"] = entities["document_type"]
    for name, longest in (("full_name", 120), ("found_on", 60), ("lost_on", 60)):
        value = text_of(entities.get(name), longest=longest)
        if value:
            facts[name] = value
    description = text_of(entities.get("description"))
    if description and len(description) >= 8:
        facts["description"] = description
    proof = text_of(entities.get("proof_text"))
    if proof:
        facts["proof_text"] = proof
    if facts.get("media_key"):
        facts["photo"] = facts["media_key"]
    return ExtractedFacts(facts=facts, rejected=rejected)


FOUND = DomainSpec(
    name="found",
    roles={
        FlowName.FOUND_DECLARE: "someone who found a document and declares it",
        FlowName.FOUND_CLAIM: "someone who lost a document, then proves it is theirs",
    },
    forms={FlowName.FOUND_DECLARE: DECLARE_FORM, FlowName.FOUND_CLAIM: CLAIM_FORM},
    opening_intents={
        "FOUND_DECLARE": FlowName.FOUND_DECLARE,
        "FOUND_CLAIM": FlowName.FOUND_CLAIM,
    },
    intents=frozenset({"COLLECT_MEDIA", "COLLECT_LOCATION"}),
    rules=RULES,
    entities=ENTITIES,
    validate=validate,
)
