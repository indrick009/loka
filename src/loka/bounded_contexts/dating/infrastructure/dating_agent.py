"""The model that conducts a member's dating thread (see ``domain/dating_agent``).

Through the shared proxy, as the talk agent. Any failure answers ``None``.
"""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.dating.domain.dating_agent import MOVES, DatingMove, DatingSituation
from loka.bounded_contexts.dating.domain.profile import LOOKING
from loka.bounded_contexts.dating.infrastructure.talk_reader import OpenRouterTalkReader, _text
from loka.shared.domain.knowledge.how_idia_works import GUIDE
from loka.shared.domain.pricing import DATING_UNLOCK_SHOWN
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

_PROMPT = (
    "Tu es idia, une assistante WhatsApp au Cameroun, chaleureuse, qui tutoie, en "
    "phrases courtes. Tu conduis idia Rencontre pour {member} : idia lui présente une "
    "personne à la fois, choisie par le matching ; si les deux disent oui, ils se "
    "parlent à travers idia, numéros cachés ; le numéro se débloque quand les deux "
    "confirment et que l'homme paie ({unlock}).\n"
    "Lis le nouveau message de {member} avec la situation et les derniers échanges, "
    "comme une personne attentive, et choisis UN coup :\n"
    "- ANSWER_PROPOSAL : {member} répond à la personne proposée — liked true (oui, « je "
    "veux », « elle me plaît ») ou false (non, « elle ne me plaît pas », « un autre », "
    "« pas elle »). Si {member} dit pourquoi, mets-le dans reason (ses mots). Seulement "
    "s'il y a une personne proposée.\n"
    "- REASON : {member} explique pourquoi il a dit non à la dernière personne refusée "
    "(« le physique », « trop loin »). reason = ses mots.\n"
    "- NEXT : {member} veut voir quelqu'un et personne n'attend sa réponse.\n"
    "- SHOW_PHOTO : {member} veut revoir la photo de la personne proposée.\n"
    "- SET_LOOKING : {member} change où chercher. looking parmi {looking} : QUARTIER = "
    "son quartier, VILLE = sa ville, PAYS = tout le pays, ETRANGER = à l'étranger, "
    "PEU_IMPORTE = partout. Une autre ville précise que la sienne (« uniquement "
    "Bafoussam ») : c'est ADD_WISHES (wishes = « vivre à Bafoussam ») avec looking "
    "[\"PEU_IMPORTE\"].\n"
    "- ADD_WISHES : {member} dit ce qu'il veut ou ne veut pas chez l'autre (physique, "
    "teint, âge, caractère, religion, ville…). wishes = ses mots ; age_min / age_max "
    "si un âge est dit (un âge seul est un minimum).\n"
    "- ANSWER : une question, une remarque, une salutation, une demande qu'idia ne fait "
    "pas (plusieurs photos d'une personne : dis qu'idia en montre une, et que s'ils se "
    "disent oui tous les deux ils pourront s'en échanger), « ?? ». reply répond d'après "
    "la situation, sans rien inventer sur personne. S'il y a une personne proposée, "
    "rappelle doucement qu'elle attend un oui ou un non.\n"
    "- WITHDRAW : {member} demande clairement de supprimer son profil.\n"
    "Pour tout coup, reply peut contenir une phrase à toi avant ce que le code ajoute "
    "(la fiche de la personne suivante, la confirmation) : réagis à ce qu'il a dit, "
    "sans répéter la fiche, sans promettre ce que tu ne fais pas. Jamais de description "
    "ni de numéro de quelqu'un d'autre. Ne demande jamais « oui ou non » à un message "
    "qui répond déjà.\n"
    "Comment marche idia Rencontre : {guide}\n"
    'Réponds en JSON : {{"move": "...", "reply": "..." ou null, "liked": true/false/null, '
    '"reason": null, "looking": [], "wishes": null, "age_min": null, "age_max": null}}.'
)


class OpenRouterDatingAgent(OpenRouterTalkReader):
    async def move(self, situation: DatingSituation) -> DatingMove | None:
        system = _PROMPT.format(
            member=situation.member or "le membre",
            unlock=DATING_UNLOCK_SHOWN,
            looking=", ".join(LOOKING),
            guide=GUIDE["Rencontre"],
        )
        lines = "\n".join(
            f"{situation.member if line.speaker == 'member' else 'idia'} : {line.text}"
            for line in situation.thread
        )
        answers = "\n".join(f"- {answer}" for answer in situation.answers) or "(aucune)"
        user = (
            f"Profil de {situation.member} : {situation.profile}\n"
            f"Personne proposée, qui attend sa réponse : {situation.proposed or 'personne'}\n"
            f"Ses dernières réponses :\n{answers}\n"
            f"Où il en est : {situation.standing or 'rien en cours'}\n"
            f"Qui reste à lui proposer : {situation.left}\n\n"
            f"Derniers échanges :\n{lines or '(aucun)'}\n\n"
            f"Nouveau message de {situation.member} :\n{situation.message}"
        )
        for model in dict.fromkeys((self._settings.default_model, self._settings.fallback_model)):
            payload = await self._ask(model, system, user)
            if payload is None or payload.get("move") not in MOVES:
                continue
            move = _move(payload)
            _logger.info("dating_move", move=move.move)
            return move
        return None


def _move(payload: dict[str, Any]) -> DatingMove:
    liked = payload.get("liked")
    looking = payload.get("looking")
    return DatingMove(
        move=str(payload["move"]),
        reply=_text(payload.get("reply")),
        liked=liked if isinstance(liked, bool) else None,
        reason=_text(payload.get("reason")),
        looking=[str(item) for item in looking if item in LOOKING]
        if isinstance(looking, list)
        else [],
        wishes=_text(payload.get("wishes")),
        age_min=_age(payload.get("age_min")),
        age_max=_age(payload.get("age_max")),
    )


def _age(raw: object) -> int | None:
    if isinstance(raw, int) and not isinstance(raw, bool) and 18 <= raw <= 99:
        return raw
    return None
