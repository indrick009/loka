# Outils open source : analyse comparative et décision

Ce document répond à la section 7 de la mission : comparer cinq outils open source
candidats, et **décider, preuve à l'appui, lesquels intégrer**. Il complète
[`conversational-engine.md`](./conversational-engine.md) et
[`local-market-knowledge.md`](./local-market-knowledge.md).

## Principe de décision

La mission demande de ne rien ajouter tant que l'utilité n'est pas démontrée.
Nous partons donc d'un état de départ à battre :

- un **moteur de progression déterministe** (`PROPERTY_REQUIREMENTS`,
  `ConversationSession`) qui ne dépend jamais du LLM ;
- une **frontière unique** vers le fournisseur LLM (`OpenRouterIntentModel` +
  port de sortie) ;
- un **registre d'usage** (tokens, coût, budget) déjà en place ;
- une **base de connaissance locale** testée, qui rend le LLM inutile pour tout
  ce qui est factuel.

Un outil n'est retenu que s'il **remplace** une brique existante, s'il **réduit le
coût par appel** sans déplacer la source de vérité vers le modèle, ou s'il apporte
une garantie que le code actuel n'a pas. Sinon : rejet motivé. Chaque affirmation
porte un niveau de confiance (`VERIFIED` / `OBSERVED` / `TO_CONFIRM`) et une
source.

## Méthode

Pour chaque outil nous avons relevé, à date (octobre 2026) : la **licence** et
l'édition réellement open source, les **fonctionnalités de l'édition OSS**, le
**coût**, les **ressources** nécessaires, la **complexité d'intégration** dans
notre architecture hexagonale, la **maintenance** du projet, et l'**impact
attendu** sur nos trois problèmes observés (compréhension du jargon local,
contrôle du contexte/étape, coût en tokens). L'impact réel n'est mesuré que pour
ce que nous intégrons ; tout le reste est explicitement présenté comme une
estimation.

## Synthèse

| Outil | Licence (édition OSS) | Rôle | Édition OSS suffisante ? | Intégration | Décision |
| ----- | --------------------- | ---- | ------------------------ | ----------- | -------- |
| Rasa | Apache-2.0 (`RasaHQ/rasa`) | NLU + gestion de dialogue | **Non** (mode maintenance) | Lourde | **Rejet** |
| LangGraph | MIT (`langchain-ai/langgraph`) | Orchestration d'agents à état | Oui (observabilité payante) | Moyenne | **Rejet** (état déjà couvert) |
| Haystack | Apache-2.0 (`deepset-ai/haystack`) | Pipelines RAG / orchestration | Oui (support payant) | Moyenne | **Rejet** (pas de besoin RAG) |
| LiteLLM | OSS (tier « enterprise » séparé) | Passerelle LLM universelle | Oui | Faible–moyenne | **Veille**, non intégré |
| PromptCache | MIT (`messkan/prompt-cache`) | Cache sémantique | Oui, mais immature | Faible | **Rejet** (inadapté, risque de fuite) |

**Décision globale : aucun de ces cinq outils n'est intégré à ce stade.** Le
moteur déterministe, le port unique vers le LLM et le registre d'usage couvrent
déjà les trois problèmes visés. La suite du document justifie chaque rejet.

## 1. Rasa — rejet

- **Licence / édition.** L'ancien framework `RasaHQ/rasa` est sous Apache-2.0
  (`VERIFIED`, `github.com/RasaHQ/rasa`, fichier `LICENSE.txt`). Mais la page du
  dépôt affiche explicitement : « Rasa Open Source is currently in maintenance
  mode. The future of building AI agents with Rasa is **Hello Rasa** and
  **CALM** » (`VERIFIED`). CALM et le produit commercial ne sont pas open source.
- **Fonctionnalités OSS.** NLU à intentions, dialogue à politiques, connecteurs
  de canaux, serveur d'actions. L'édition OSS reste fonctionnelle mais **gelée**.
- **Coût.** Entrée de gamme à 0 € (auto-hébergé), mais l'édition commerciale
  (CALM, support, limites de conversations) est facturée (`OBSERVED`).
- **Ressources.** Python lourd, entraînement NLU, serveur d'actions séparé,
  base d'entraînement à maintenir.
- **Intégration.** Élevée : Rasa veut posséder la boucle de dialogue, exactement
  la brique que nous **voulons garder en code** (`ConversationSession`). Adopter
  Rasa reviendrait à déplacer la source de vérité hors de notre domaine.
- **Maintenance.** Produit principal en maintenance ; la valeur a migré vers une
  offre fermée.
- **Impact attendu.** Aucun gain sur nos trois défauts : la progression resterait
  à réimplémenter dans son modèle, et le jargon local n'est pas une intention
  d'entraînement mais une donnée — nous l'avons déjà codée.
- **Décision.** **Rejet.** Contredit la règle « le LLM n'est jamais la source de
  vérité » et repose sur une base open source abandonnée.

## 2. LangGraph — rejet (état déjà couvert)

- **Licence / édition.** MIT (`VERIFIED`, `langchain-ai/langgraph`). Le projet se
  décrit comme un « low-level orchestration framework for building stateful
  agents », avec **exécution durable** (reprise exacte après panne) et mémoire
  persistante. L'observabilité et le déploiement passent par **LangSmith**,
  produit commercial distinct (`VERIFIED`).
- **Fonctionnalités OSS.** Graphes d'exécution à état, *checkpointing*
  (reprise), *interrupts* (human-in-the-loop), mémoire court/long terme.
- **Coût.** Bibliothèque gratuite ; observabilité et déploiement managé payants
  (`OBSERVED`).
- **Ressources.** Modéré ; un *checkpointer* persistant demande Redis/Postgres.
- **Intégration.** Moyenne mais **redondante** : nous avons déjà
  `ConversationSession` + `FlowStep` persistés, qui survivent à un redéploiement.
  Introduire un second état persistant créerait **deux sources de vérité**.
- **Maintenance.** Très active (42,9k étoiles, monorepo `libs/`).
- **Impact attendu.** Nul sur le jargon et le coût ; la gestion d'étape est déjà
  résolue en code déterministe. Le seul apport réel (reprise après incident) est
  déjà obtenu par la persistance de session.
- **Décision.** **Rejet.** Bon outil, mais il duplique notre brique la plus
  sensible. À réévaluer uniquement si l'on construisait un agent multi-outils
  long, ce qui n'est pas le besoin.

## 3. Haystack — rejet (pas de besoin RAG)

- **Licence / édition.** Apache-2.0 (`VERIFIED`, `deepset-ai/haystack`).
  Fonctionnalités OSS : pipelines modulaires, composants de récupération/agents,
  support natif de l'asynchrone, agnostique du modèle. Une offre « Haystack
  Enterprise » (support, plateforme) existe séparément (`VERIFIED`).
- **Coût.** Bibliothèque gratuite ; support/plateforme payants (`OBSERVED`).
- **Ressources.** Modéré, mais un usage RAG classique implique un magasin de
  documents et un index vectoriel.
- **Intégration.** Moyenne ; orientée « contexte enrichi par récupération »,
  ce qui suppose des documents à retrouver.
- **Maintenance.** Très active (26,7k étoiles), écosystème d'intégrations.
- **Impact attendu.** Faible : notre connaissance locale est une **table
  structurée** (quartier → ville, fourchettes de prix, glossaire), pas un corpus
  à retrouver. Un pipeline RAG pour lire un dictionnaire serait surdimensionné
  et ajouterait latence et coût d'embeddings.
- **Décision.** **Rejet.** Aucun besoin de recherche documentaire à ce stade ;
  notre `knowledge/market.py` remplit le rôle, sans dépendance réseau.

## 4. LiteLLM — veille, non intégré

- **Licence / édition.** Le dépôt OSS (`VERIFIED`, `BerriAI/litellm`) expose un
  « AI Gateway » auto-hébergé ; un tier « Enterprise » existe à part
  (`VERIFIED`). Le projet se présente comme « Rust core with Python SDK »
  (`VERIFIED`).
- **Fonctionnalités OSS.** Appel de 100+ fournisseurs au format OpenAI,
  suivi de coût, *guardrails*, *load balancing*, journalisation, une latence P95
  annoncée à 8 ms (`VERIFIED`, README).
- **Coût.** Bibliothèque/proxy gratuits ; tier entreprise payant (`OBSERVED`).
- **Ressources.** Faible à moyen : un SDK ou un proxy séparé.
- **Intégration.** Faible–moyenne, grâce au format OpenAI — mais elle créerait
  une **deuxième passerelle** là où nous avons déjà un port unique
  (`OpenRouterIntentModel`) et un registre d'usage.
- **Maintenance.** Très active (60,4k étoiles) mais grande surface (1,8k issues
  ouvertes, `OBSERVED`).
- **Impact attendu.** Deux bénéfices potentiels : (a) *fallback* multi-fournisseur
  en cas d'indisponibilité d'OpenRouter ; (b) mutualiser *cache*/*guardrails*.
  Aucun des deux n'est un de nos trois défauts. Le suivi de coût, lui, est **déjà**
  assuré par notre registre.
- **Décision.** **Veille.** Non intégré maintenant (surface d'attaque et
  dépendance en plus pour un gain non démontré). À reconsidérer si l'on ajoute un
  second fournisseur, avec un test A/B du coût et de la latence avant adoption.

## 5. PromptCache — rejet (inadapté et risque de fuite)

- **Licence / édition.** MIT (`VERIFIED`, `messkan/prompt-cache`), proxy Go
  auto-hébergé, stockage BadgerDB, v0.4.0 (`VERIFIED`).
- **Fonctionnalités OSS.** Cache sémantique à deux seuils (haut = coup sûr,
  bas = miss, zone grise = vérification par petit modèle), endpoints compatibles
  OpenAI, métriques Prometheus (`VERIFIED`).
- **Coût.** Gratuit ; mais un hit évite un appel fournisseur, donc l'impact se
  mesure en **taux de succès du cache**.
- **Ressources.** Faible (Go, binaire unique + BadgerDB).
- **Intégration.** Faible techniquement, **mais sémantiquement incompatible** :
  nos requêtes au LLM portent l'**état de conversation** dans le prompt. Deux
  tours ne sont presque jamais interchangeables : le taux de succès attendu est
  faible (`TO_CONFIRM`, dépend de la distribution réelle).
- **Maintenance.** Jeune : 428 étoiles, 47 commits, un seul mainteneur
  (`OBSERVED`). L'auteur avertit lui-même : « The current cache/index
  architecture is process-wide and does not provide built-in tenant namespaces »
  et « **Semantic similarity is not an authorization mechanism** » (`VERIFIED`,
  README et `RESPONSIBLE_USE.md`). La cache par défaut est en clair, TTL 24 h.
- **Impact attendu.** Risque élevé pour un gain incertain : mettre en cache des
  échanges entre bailleurs et locataires dans un composant **sans isolation
  multi-tenant** expose des données d'un utilisateur à un autre si un match
  sémantique se trompe. C'est exactement le type de décision que la mission nous
  demande de ne pas prendre à la légère.
- **Décision.** **Rejet.** Inadapté à des réponses dépendantes de l'état, et
  contraire à l'exigence d'isolation. Toute mise en cache de ce type devrait être
  conçue *dans* le domaine, avec clé d'isolation par session, pas dans un proxy
  sémantique global.

## Ce que nous faisons à la place

Les trois défauts de la mission sont traités **dans le code existant**, ce qui
est aussi la réponse à la contrainte de coût :

- **Jargon / compréhension locale** → base de connaissance codée et testée
  (`knowledge/market.py`, normalisation des prix « mil / mille / k / millions / mio »).
- **Contexte et étape** → moteur déterministe (`PROPERTY_REQUIREMENTS`,
  `pending_answer`, `_opens_new_flow`) : le LLM propose, le code décide.
- **Coût en tokens** → raccourcis déterministes qui évitent l'appel LLM quand la
  réponse est certaine, plus un registre d'usage (tokens/coût/budget).

## Conditions de réévaluation

Un outil ne serait réexaminé que si l'un de ces événements survenait, avec
mesure avant/après à l'appui :

1. Besoin d'un **second fournisseur LLM** ou d'un *fallback* automatique →
   LiteLLM redevient pertinent.
2. Apparition d'un **vrai corpus documentaire** à interroger (annonces,
   réglementation) → Haystack.
3. Besoin d'**agents longs multi-étapes** avec reprise après incident et
   parallélisme → LangGraph.
4. Besoin d'un **cache** à grande échelle → conçu dans le domaine avec clé
   d'isolation par session, jamais un proxy sémantique global.

## Sources

| Outil | Source consultée |
| ----- | ---------------- |
| Rasa | `github.com/RasaHQ/rasa` (LICENSE, README) |
| LangGraph | `github.com/langchain-ai/langgraph` (LICENSE, README) |
| Haystack | `github.com/deepset-ai/haystack` (LICENSE, README) |
| LiteLLM | `github.com/BerriAI/litellm` (LICENSE, README) |
| PromptCache | `github.com/messkan/prompt-cache` (LICENSE, README, `RESPONSIBLE_USE.md`) |
