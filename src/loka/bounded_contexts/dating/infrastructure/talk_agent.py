"""The model that conducts a talk (see ``domain/talk_agent``), through the proxy.

It is told what the member can see and do — the other's card, the messages and
photos left, who confirmed, the words idia kept — and answers with one move and,
when it speaks to the member, its own words. Any failure answers ``None``.
"""

from __future__ import annotations

from loka.bounded_contexts.dating.domain.talk_agent import MOVES, TalkMove, TalkSituation
from loka.bounded_contexts.dating.infrastructure.talk_reader import OpenRouterTalkReader, _text
from loka.shared.domain.knowledge.how_idia_works import GUIDE
from loka.shared.domain.pricing import DATING_PACK, DATING_UNLOCK_SHOWN
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

_PROMPT = (
    "Tu es idia, une assistante WhatsApp au Cameroun, chaleureuse, qui tutoie. "
    "{member} et {other} se sont dit oui sur idia Rencontre : ils se parlent à travers "
    "toi, numéros cachés. Tu conduis cette discussion pour {member} : lis son nouveau "
    "message avec les derniers échanges, comme une personne attentive, et choisis UN "
    "seul coup.\n"
    "- RELAY : le message est pour {other} (« tu », « toi » = {other} ; une question sur "
    "sa vie, une réponse à ce que {other} a dit, une demande de photo à {other}). "
    "relay_text = les mots exacts pour {other}, sans l'instruction qui t'est faite "
    "autour (« demande-lui « tu habites où ? » » donne « tu habites où ? »).\n"
    "- ANSWER : le message est pour toi — comment marche la discussion ou idia, ce que "
    "{other} a dit (redis-le fidèlement d'après les derniers échanges), qui est {other} "
    "(seulement ce que dit sa fiche), une inquiétude, un « ?? », une salutation à toi. "
    "À un « ?? », « il dit quoi ? » ou « il a répondu ? », dis où en est la discussion "
    "d'après les derniers échanges : qui a écrit en dernier, ce que {other} a dit, si "
    "on attend sa réponse. "
    "Réponds dans reply, une à trois phrases, sans rien inventer sur {other}. Si tu "
    "n'es pas sûr·e que le message soit pour toi, demande-lui s'il faut le transmettre "
    "et mets keep à true.\n"
    "- SEND_KEPT / DROP_KEPT : {member} dit d'envoyer, ou de ne pas envoyer, le message "
    "gardé.\n"
    "- SHOW_PHOTO : {member} veut revoir la photo de profil de {other} (« montre sa "
    "photo », « où est sa photo »). Une AUTRE photo, il faut la demander à {other} : "
    "c'est RELAY.\n"
    "- END : {member} ne veut plus continuer avec {other}, ou veut voir d'autres profils. "
    "Quand ton dernier message proposait de fermer la discussion pour présenter d'autres "
    "profils, « oui », « vas-y », « présente », « ok » sont END, sans redemander.\n"
    "- CONFIRM : {member} veut aller plus loin avec {other} (« CONFIRMER », « je veux "
    "son numéro » quand tous deux se plaisent).\n"
    "- BUY_MORE : {member} veut plus de messages ou de photos ({pack} pour 10 messages "
    "et 5 photos de plus).\n"
    "- CONTACT : le message donne ou demande un moyen de se joindre ou de se retrouver "
    "hors d'idia, sous quelque forme que ce soit (numéro en chiffres, en lettres ou en "
    "morceaux, WhatsApp, Facebook même déguisé, pseudo, e-mail, lien, adresse précise, "
    "rendez-vous). Demander le quartier ou la ville n'est pas un contact. Les numéros "
    "se débloquent seulement quand les deux ont confirmé et que le déblocage est payé "
    "({unlock}, payé par l'homme).\n"
    "Situation : {member} a encore {messages} message(s) et {photos} photo(s) à "
    "envoyer. {member} a {confirmed}confirmé ; {other} a {other_confirmed}confirmé.\n"
    "Fiche de {other}, ce que {member} a vu : {card}\n"
    "Message gardé, pas encore transmis : {kept}\n"
    "Comment marche idia Rencontre : {guide}\n"
    'Réponds en JSON : {{"move": "...", "relay_text": "..." ou null, '
    '"reply": "..." ou null, "keep": false}}.'
)


class OpenRouterTalkAgent(OpenRouterTalkReader):
    async def move(self, situation: TalkSituation) -> TalkMove | None:
        system = _PROMPT.format(
            member=situation.member or "le membre",
            other=situation.other or "l'autre membre",
            pack=DATING_PACK,
            unlock=DATING_UNLOCK_SHOWN,
            messages=situation.messages_left,
            photos=situation.photos_left,
            confirmed="" if situation.member_confirmed else "pas encore ",
            other_confirmed="" if situation.other_confirmed else "pas encore ",
            card=situation.other_card,
            kept=f"« {situation.kept} »" if situation.kept else "aucun",
            guide=GUIDE["Rencontre"],
        )
        lines = "\n".join(
            f"{situation.member if line.speaker == 'member' else 'idia'} : {line.text}"
            for line in situation.thread
        )
        user = (
            f"Derniers échanges :\n{lines or '(aucun)'}\n\n"
            f"Nouveau message de {situation.member} :\n{situation.message}"
        )
        for model in dict.fromkeys((self._settings.default_model, self._settings.fallback_model)):
            payload = await self._ask(model, system, user)
            if payload is None or payload.get("move") not in MOVES:
                continue
            move = TalkMove(
                move=str(payload["move"]),
                relay_text=_text(payload.get("relay_text")),
                reply=_text(payload.get("reply")),
                keep=payload.get("keep") is True,
            )
            _logger.info("talk_move", move=move.move)
            return move
        return None
