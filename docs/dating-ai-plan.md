# idia Rencontre piloté par l'IA — plan

Statut : plan, rien de construit. Branche `feat/dating-agent`. Rien en prod avant le test (étape 4).
Ouvert : on ajoute ici toute nouvelle idée avant de commencer.

## Ce qu'on retient (27 nov. 2026)

- Le code décidait, l'IA classait : chaque cas raté ajoutait une règle. On arrête cette route.
- L'IA comprend bien un « non » ; les erreurs venaient du code autour d'elle.
- L'IA ne voyait que la conversation, pas le dating (qui est là, qui attend, qui est oublié).
- Pas tout en mémoire : l'IA **cherche** avec des outils, lit, puis décide (RAG agentique / ReAct).
- Le code garde seulement les invariants :
  - un oui explicite ;
  - aucun contact avant un match confirmé et payé ;
  - les filtres absolus (sexe, âge, déjà vu, match en cours) ;
  - le paiement (`pricing.py`).
- Un test par messages figés ne suffit pas : une IA doit jouer le membre.

## Étapes

### 0. La photo, contrôlée avant de terminer le profil

**Vérifications sans IA**
- flou ;
- résolution ;
- visage : aucun, plusieurs, ou trop petit (YuNet) ;
- doublon (SFace).

**Lecture par l'IA, en un seul appel**
- capture d'écran TikTok, Facebook, Instagram ou WhatsApp ;
- photo d'un écran ;
- image d'internet ou célébrité ;
- photo sombre, filtre fort, visage caché ;
- contact visible ;
- ce que l'IA voit de la personne : teint, âge apparent, allure. C'est rangé pour le matching, jamais affiché, et utilisé seulement pour respecter un critère dit par l'autre membre.

**Ce que le code fait**
- Bloquant, il faut une autre photo :
  - pas de visage ;
  - capture d'écran ;
  - image d'internet ;
  - contact visible ;
  - personne qui paraît mineure.
- Conseil donné une fois, puis la photo est acceptée : floue, sombre, filtre, plusieurs personnes.
- Âge apparent très loin de l'âge déclaré : le profil est accepté et marqué pour vérification.
- Les mots de la réponse viennent de l'IA ; le code décide seulement entre bloquant et conseil.

**Profils existants**
- Analysés une fois.
- Un message seulement aux cas bloquants, et après accord.

### 1. L'index des profils

- Un résumé texte par profil : fiche, description, lecture de la photo, critères, raisons de ses refus.
- Une empreinte pgvector par résumé, mise à jour à chaque changement.

### 2. L'entremetteuse IA

- Ses outils : `chercher_membres`, `lire_profil`, `historique`, `oublies`, `proposer`.
- Elle se déclenche :
  - aux événements : inscription, refus, nouveau critère ;
  - chaque matin, pour les oubliés.
- `proposer` vérifie les invariants. Chaque proposition est enregistrée avec sa raison.

### 3. L'agent de conversation

- Une boucle d'outils (Pydantic AI, via le proxy) : il agit, voit le résultat, puis écrit.
- Il demande à l'entremetteuse qui proposer.
- Une note courte par membre, tenue par l'IA, sert de mémoire à la place de tout l'historique.
- On supprime les rustines de la branche : `_says_yes`, `_GENERIC`, `_reaction`, etc.

### 4. Le test avant la prod

- Copie de la base, Redis et RabbitMQ séparés, envois capturés, paiement simulé.
- Horloge accélérée sur 24 h, avec les relances et le passage du matin.
- Une IA joue chaque membre réel d'après sa vraie conversation (idée d'ArkSim et de la simulation d'utilisateurs de Google ADK).
- Contrôles automatiques :
  - personne oublié ;
  - aucune répétition ;
  - aucun oui non dit ;
  - aucun contact qui fuit.
- Un juge IA note chaque conversation.
- Les photos de test sont les nôtres uniquement.

## Suivi après la mise en prod

**Chiffres quotidiens**
- membres sans proposition depuis plus de 24 h ;
- répétitions ;
- délai entre l'inscription et la première proposition.

**Coût**
- un appel d'IA par message ;
- l'entremetteuse seulement aux événements.

**Peu d'hommes de plus de 25 ans** : l'IA le dit franchement et propose d'élargir.

## Outils retenus

- **pgvector** : dans la base actuelle.
- **Pydantic AI** : boucle d'outils typée, avec des faux modèles pour les tests.

Écartés : OpenAI Agents SDK, Agno, ADK, AgentScope + Mem0 + ChromaDB, parce qu'ils sont trop lourds.

## Idées à ajouter

-
