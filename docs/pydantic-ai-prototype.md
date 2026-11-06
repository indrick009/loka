# Prototype Pydantic AI : la recherche locataire menée par le modèle

Branche `pydantic-ai-integration`. Prototype **hors production** : il n'est pas
branché sur WhatsApp ni sur la base. Il sert à répondre à une question :

> Un modèle récent, à qui l'on donne nos règles métier sous forme d'outils,
> tient-il une meilleure conversation que le classifieur + les textes figés ?

## Le principe

Aujourd'hui, le pipeline lit d'abord le message avec des règles (≈ 88
expressions régulières), le modèle classe et extrait, et la réponse sort
souvent d'un texte figé. Le prototype inverse l'ordre :

| Qui | Fait quoi |
| --- | --- |
| **Le modèle** | comprend le message (salutation, réponse, correction, question sur ce qui a été dit, hors-sujet…) et rédige la réponse avec ses mots |
| **Le code** (outils) | valide chaque fait (`FactExtractor`, les mêmes value objects que le pipeline), dit ce qui manque encore (`SEARCH_REQUIREMENTS`), interroge le catalogue |
| **Le code** (après le modèle) | ajoute la liste des logements telle que le catalogue la donne (`results_text`) |

La frontière de `docs/conversational-engine.md` est conservée :

- `record_facts` : le modèle **propose** des faits ; un fait refusé (quartier
  inconnu, loyer de « 50 » FCFA) n'est jamais stocké, le refus lui est renvoyé
  pour qu'il reformule la question.
- `search_listings` refuse de chercher tant que les quatre questions du
  locataire (type, zone, loyer max, mois d'avance) ne sont pas répondues.
- Le modèle n'écrit jamais une annonce : la liste numérotée est ajoutée par le
  code.
- Un montant que ni l'utilisateur ni le catalogue n'a donné (« votre budget est
  de 150 000 » alors qu'il a dit 60 000) est refusé et le modèle doit réécrire
  sa réponse.
- Les faits connus sont injectés dans les instructions à chaque tour : le
  modèle ne peut pas « oublier » le budget ni prétendre ne pas avoir de mémoire.

## Fichiers

- `src/loka/bounded_contexts/ai/infrastructure/agent/tenant_search.py` — l'agent, ses outils et ses garde-fous
- `src/loka/bounded_contexts/ai/infrastructure/agent/models.py` — le modèle via la clé OpenRouter existante
- `src/loka/bounded_contexts/ai/infrastructure/agent/demo_catalogue.py` — le Yaoundé de démo, en mémoire
- `src/loka/interfaces/cli/agent_chat.py` — discuter avec l'agent dans un terminal
- `tests/unit/conversation/test_tenant_search_agent.py` — les garde-fous, testés sans réseau
- `tests/fixtures/agent/tenant_fr.txt` — une conversation type pour comparer les modèles

## Essayer

```bash
uv sync --extra dev --extra agent
export AI__API_KEY=sk-or-...           # la clé OpenRouter de Kora

# discussion libre
uv run python -m loka.interfaces.cli.agent_chat --model <id-openrouter>

# rejouer la conversation type, pour comparer plusieurs modèles
uv run python -m loka.interfaces.cli.agent_chat --model <id-openrouter> \
    --script tests/fixtures/agent/tenant_fr.txt
```

Les identifiants de modèle sont ceux de https://openrouter.ai/models. Sans
`--model`, c'est `AI__DEFAULT_MODEL` (aujourd'hui `google/gemini-2.5-flash`).

Dans la discussion : `/pin 3.848 11.502` simule une position WhatsApp
partagée, `/facts` affiche les faits retenus, `/reset` recommence.

## Ce qui reste avant la production

1. **Comparer** : rejouer les conversations de `docs/conversation-defects-v1.md`
   avec 2 ou 3 modèles récents, mesurer coût et latence par tour.
2. **Brancher derrière un drapeau** (`AI__AGENT_ENABLED`) dans
   `AnalyseConversationMessageUseCase`, pour le seul flux `PROPERTY_SEARCH` :
   historique reconstruit depuis `ConversationExchanges`, faits relus et écrits
   dans `session.context`, coût passé au `AiUsageLedger`, budget respecté.
3. **Étendre** au flux bailleur (10 étapes), puis à Food, Dating et Found — même
   recette : les règles métier deviennent des outils, le modèle mène.
