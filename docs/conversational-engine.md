# Moteur conversationnel : audit, modèle de progression et coût

Ce document explique les trois défauts corrigés dans le moteur conversationnel,
le modèle de progression retenu, la frontière entre code et LLM, et la stratégie
de réduction des tokens. Il complète
[`local-market-knowledge.md`](./local-market-knowledge.md).

## 1. Audit — trois défauts, une même racine

Les trois problèmes venaient de la même confusion : **le modèle était chargé de
décisions qui appartiennent au code**.

### Défaut 1 — La question « où ? » ne pouvait pas se fermer

`FactExtractor.validate` ne construisait une `location` que si une `city` était
présente. Or un Camerounais répond par le **quartier** (« Bastos »). La valeur
partait dans `location_hint`, la `location` restait absente, et
`PROPERTY_REQUIREMENTS` — qui teste `location` — redemandait la localisation
**indéfiniment**. Le formulaire ne pouvait pas avancer sur une réponse pourtant
complète.

### Défaut 2 — Un formulaire qui ne pouvait jamais publier

Le formulaire collectait `property_type`, `standing`, `location`, `rent`. Mais
`Property.blocking_issues()` exige aussi `charges`, `deposit`,
`minimum_duration`, `availability` et `conditions`. Les étapes et les textes
existaient déjà mais n'étaient **jamais atteints**, et le `RESPONSE_SCHEMA`
n'était pas capable d'extraire ces champs. Un bailleur pouvait donc confirmer un
bien… que la plateforme refusait ensuite de publier.

### Défaut 3 — Pas de connaissance locale structurée

Les quartiers reposaient sur une expression régulière d'une douzaine de mots, et
les prix sur une table statique par type. Aucun index quartier → ville, aucune
gestion de l'argot local (« mil »), d'où des parses absurdes (« 50 » compris
comme 50 FCFA).

## 2. Modèle de progression

L'ordre est la conversation. Il est décidé **en code** par
`PROPERTY_REQUIREMENTS`, jamais par le LLM. L'ordre suit la façon dont on décrit
un logement ici : d'abord le type, puis le standing, puis le lieu, puis le loyer,
puis les conditions d'entrée.

| # | Champ | Étape | Question |
| - | ----- | ----- | -------- |
| 1 | `property_type` | `COLLECT_PROPERTY_TYPE` | `ask.property_type` |
| 2 | `standing` | `COLLECT_STANDING` | `ask.standing` |
| 3 | `location` | `COLLECT_LOCATION` | `ask.location` |
| 4 | `rent` | `COLLECT_PRICE` | `ask.rent` |
| 5 | `charges` | `COLLECT_CHARGES` | `ask.charges` |
| 6 | `deposit` | `COLLECT_DEPOSIT` | `ask.deposit` |
| 7 | `minimum_duration_months` | `COLLECT_MINIMUM_DURATION` | `ask.minimum_duration` |
| 8 | `availability` | `COLLECT_AVAILABILITY` | `ask.availability` |
| 9 | `conditions` | `COLLECT_CONDITIONS` | `ask.conditions` |
| — | — | `CONFIRM_PROPERTY` | `ask.confirm_property` |

Règles :

- `_first_missing` parcourt l'ordre et renvoie la première étape absente **des
  faits du message courant et du contexte déjà connu** (`known`). Sans `known`,
  un bailleur qui donne sa ville après avoir donné le type se ferait redemander
  le type : le formulaire redémarrerait à chaque tour.
- L'avance (« avance de 3 mois ») et les frais d'agence sont saisis dans
  `conditions` (texte libre) ; la caution correspond au champ `deposit`. Aucun
  changement de schéma de base n'a été nécessaire : les colonnes existaient déjà.
- Une étape n'est **jamais** sautée, mais un champ peut arriver plus tôt : si
  le message contient déjà la valeur, `_first_missing` passe simplement au
  suivant.

## 3. Résolution du lieu

`_build_location` : si la ville est absente mais qu'un quartier est présent, le
quartier est résolu via `market.resolve_neighbourhood`. Un quartier connu
(Bastos) produit une `location` complète ; un lieu inconnu (« Zzzville ») reste
un *indice* (`location_hint`) et **n'est jamais promu en ville** — c'est
exactement ainsi qu'une annonce erronée entrerait dans la recherche.

## 4. Frontière de confiance : qui décide quoi

| Décision | Responsable |
| -------- | ----------- |
| Comprendre l'intention, extraire des entités candidates | LLM |
| Ouvrir un flux (`CREATE_PROPERTY`, `PROPERTY_SEARCH`) | intention, mappée en code |
| Valider chaque valeur via les value objects du domaine | `FactExtractor` |
| Ordre des questions, étape suivante | `PROPERTY_REQUIREMENTS` (code) |
| Reformulation du message sortant | couche outbound (`whatsapp_replies`) |

Le LLM n'est jamais cru sur parole :

- Une intention hors de `SUPPORTED_INTENTS` est traitée comme non fiable.
- Une confiance < `MIN_ACCEPTED_CONFIDENCE` (0,75) n'avance pas la conversation.
- Un champ refusé est **abandonné, jamais réparé** ; la question sera reposée.
- Le numéro de téléphone n'est jamais mis dans le prompt (les prompts finissent
  dans les logs des fournisseurs).

## 5. Stratégie de réduction des tokens

Chaque tour renvoie un prompt complet : tout ce qui ne peut plus influencer la
réponse est du coût pur.

- `allowed_intents` supprimé : l'énumération vit **une seule fois** dans
  `schema.intent`. Le `SYSTEM_PROMPT` renvoie à cette énumération.
- `required_fields` ne contient plus que les champs **encore manquants** (glosés
  par flow), au lieu des quatre champs fixes.
- `price_reference_monthly_xaf` n'est envoyé que **tant que le loyer est
  inconnu** ; une fois le prix connu, la fourchette ne sert plus.
- `recent_turns` est omis s'il est vide.
- Les clés internes (`next_question`, `abort_reason`, tout ce qui commence par
  `_`) sont retirées de `known_facts` avant l'envoi.
- Voie rapide déterministe : oui / non / prix seul / salutation sont traités
  **sans appel modèle** (0 token), le tour étant tout de même enregistré.

Le test `test_the_prompt_omits_context_that_can_no_longer_be_used` verrouille ces
invariants pour qu'une refonte ne réintroduise pas silencieusement la charge.

## 6. Couverture de tests

- `tests/unit/ai/test_fact_extraction.py` : validation de chaque champ, ordre du
  formulaire, résolution des quartiers, refus d'un lieu inconnu.
- `tests/unit/ai/test_analyse_conversation_message.py` : progression, voie
  rapide (salutation), clôture de la question de lieu sur un quartier nu,
  invariants de taille du prompt.
- `tests/unit/property/test_conversation_listing_facts.py` : les faits
  confirmés hydratent l'agrégat et **laissent uniquement les photos manquantes**
  — la preuve que le formulaire collecte assez pour publier.
- `tests/unit/test_whatsapp_replies.py` : chaque clé de question a un texte.
