# MISSION

Construire une plateforme immobilière conversationnelle permettant aux **bailleurs** et **locataires** de gérer la location de logements principalement via WhatsApp.

La plateforme doit être pensée comme un véritable produit de production capable de supporter une très forte croissance, avec une architecture **DDD + SOLID + Clean/Hexagonal Architecture**, une excellente performance, une forte observabilité et un système antifraude natif.

L'objectif n'est PAS de construire un simple chatbot.

WhatsApp est l'interface conversationnelle.

Le backend contient le véritable système métier.

L'IA aide à comprendre les intentions, extraire les informations et guider l'utilisateur, mais **l'IA ne doit jamais devenir la source de vérité du métier**.

---

# 1. STACK IMPOSÉE

Utiliser principalement :

- Python
- FastAPI
- PostgreSQL
- Redis
- RabbitMQ
- Baileys pour WhatsApp
- OpenRouter pour l'accès aux modèles LLM
- Gemini 2.5 comme modèle principal initial
- Docker / Docker Compose pour le développement
- Pydantic
- SQLAlchemy 2.x ou équivalent moderne adapté à une architecture DDD
- Alembic pour les migrations
- pytest
- asyncio

Baileys :

https://github.com/WhiskeySockets/Baileys

OpenRouter :

https://openrouter.ai/

L'intégration OpenRouter doit être abstraite derrière une interface afin de pouvoir remplacer Gemini par un autre modèle sans modifier le domaine.

Exemple conceptuel :

LLMProvider
→ OpenRouterProvider
→ GeminiProvider configuration

Le domaine ne doit jamais dépendre directement de Gemini, OpenRouter ou Baileys.

---

# 2. CONTRAINTE MAJEURE : PERFORMANCE

La plateforme doit être conçue pour pouvoir évoluer jusqu'à environ :

**10 millions d'utilisateurs actifs/jour**

Ce chiffre doit être considéré comme une contrainte de capacité future, pas comme une hypothèse de concurrence instantanée.

L'architecture doit donc permettre :

- horizontal scaling ;
- plusieurs instances FastAPI ;
- plusieurs workers WhatsApp ;
- plusieurs consumers RabbitMQ ;
- Redis distribué ;
- PostgreSQL correctement indexé ;
- séparation lecture/écriture lorsque nécessaire ;
- cache ;
- traitement asynchrone ;
- backpressure ;
- rate limiting ;
- idempotence ;
- retries contrôlés ;
- circuit breakers ;
- timeouts ;
- pagination ;
- batch processing ;
- connection pooling.

NE JAMAIS faire de traitement lourd directement dans une requête HTTP ou dans le handler WhatsApp.

Les tâches lourdes doivent être envoyées dans RabbitMQ.

Exemples :

- analyse antifraude ;
- traitement d'images ;
- OCR ;
- vérification documentaire ;
- notifications ;
- analyse conversationnelle secondaire ;
- statistiques ;
- génération de rapports ;
- recalcul de scores ;
- événements métier secondaires.

---

# 3. PRINCIPES ARCHITECTURAUX

Respecter strictement :

- Domain-Driven Design ;
- SOLID ;
- Clean Architecture ;
- Hexagonal Architecture ;
- séparation stricte des responsabilités ;
- Dependency Inversion ;
- CQRS lorsque cela apporte réellement un bénéfice ;
- Event-Driven Architecture lorsque pertinent.

Ne PAS créer une architecture artificiellement complexe.

Le domaine doit rester indépendant des frameworks.

Le domaine ne doit connaître :

- ni FastAPI ;
- ni SQLAlchemy ;
- ni PostgreSQL ;
- ni Redis ;
- ni RabbitMQ ;
- ni Baileys ;
- ni OpenRouter ;
- ni Gemini.

---

# 4. BOUNDED CONTEXTS

Identifier explicitement les Bounded Contexts.

Une première proposition :

## Identity & Access

Responsable de :

- utilisateur ;
- identité ;
- authentification ;
- rôles ;
- permissions ;
- numéro WhatsApp ;
- statut du compte.

Rôles principaux :

- TENANT
- LANDLORD
- ADMIN
- MODERATOR
- FRAUD_ANALYST

---

## Landlord Verification

Responsable de :

- vérification d'identité ;
- CNI ;
- selfie ;
- statut de vérification ;
- vérification manuelle ;
- vérification automatique ;
- historique des vérifications.

Statuts possibles :

PENDING
UNDER_REVIEW
VERIFIED
REJECTED
SUSPENDED

La vérification doit être séparée du domaine immobilier.

---

## Property

Responsable de :

- biens ;
- logements ;
- caractéristiques ;
- localisation ;
- photos ;
- vidéos ;
- équipements ;
- prix ;
- charges ;
- disponibilité ;
- règles du bailleur ;
- durée minimale ;
- statut.

Exemples :

AVAILABLE
RESERVED
RENTED
UNAVAILABLE
SUSPENDED

---

## Search

Responsable de :

- recherche ;
- filtres ;
- recherche conversationnelle ;
- classement ;
- disponibilité ;
- recommandations.

La recherche doit être optimisée pour la lecture.

Ne jamais faire une recherche naïve sur toutes les propriétés.

Prévoir les bons indexes et éventuellement une solution de search dédiée si le volume l'exige.

---

## Rental Application

Responsable de :

- intérêt du locataire ;
- demande de location ;
- proposition ;
- négociation ;
- acceptation ;
- refus.

---

## Visit

Responsable de :

- demande de visite ;
- proposition de date ;
- confirmation ;
- annulation ;
- présence ;
- historique.

---

## Payment / Service Fee

Responsable des frais de service de 1 000 FCFA.

Le domaine ne doit pas être couplé directement au fournisseur de paiement.

Créer une abstraction :

PaymentGateway

Puis adapter le fournisseur utilisé.

---

## Messaging / WhatsApp

Responsable de :

- réception des messages WhatsApp ;
- envoi ;
- sessions ;
- mapping numéro → utilisateur ;
- conversation ;
- delivery status ;
- retry.

Baileys doit rester dans l'infrastructure.

---

## Fraud & Trust

Context extrêmement important.

Responsable de :

- fraud detection ;
- risk scoring ;
- comportements suspects ;
- comptes suspects ;
- biens suspects ;
- conversations suspectes ;
- anomalies ;
- signalements ;
- investigations.

---

## AI Conversation

Responsable de :

- compréhension de l'intention ;
- extraction des données ;
- classification ;
- génération de réponses ;
- désambiguïsation.

L'IA ne doit PAS décider seule des actions critiques.

Exemple :

Gemini peut comprendre :

> "Je veux louer mon appartement à Bastos pour 250000."

Mais seul le domaine décide réellement :

CREATE_PROPERTY_DRAFT

---

# 5. WHATSAPP / BAILEYS

Baileys doit être considéré comme un adapter d'infrastructure.

Architecture :

WhatsApp
↓
Baileys
↓
WhatsApp Adapter
↓
Application Layer
↓
Domain

Jamais :

Domain
↓
Baileys

Les événements WhatsApp doivent être normalisés.

Exemple :

WhatsAppMessageReceived

contenant :

- external_message_id ;
- phone_number ;
- timestamp ;
- message_type ;
- text ;
- media_reference ;
- metadata.

Le système doit être **idempotent**.

Si WhatsApp envoie deux fois le même événement, le système ne doit pas créer deux demandes.

Utiliser un unique constraint sur l'identifiant externe du message.

---

# 6. IA / OPENROUTER

Utiliser OpenRouter comme gateway LLM.

Gemini 2.5 est le modèle initial.

Mais ne jamais coder :

if model == "gemini..."

dans le domaine.

Créer une abstraction :

AIProvider

avec par exemple :

understand_intent()
extract_entities()
generate_response()
classify_message()

Le modèle doit retourner autant que possible des sorties structurées.

Exemple :

{
  "intent": "CREATE_PROPERTY",
  "confidence": 0.97,
  "entities": {
    "property_type": "APARTMENT",
    "location": "Bastos",
    "bedrooms": 2,
    "price": 250000
  }
}

L'application valide toujours les données avant de déclencher une commande métier.

---

# 7. NE PAS UTILISER L'IA POUR TOUT

Pour réduire les coûts et améliorer les performances :

Utiliser d'abord :

- règles ;
- regex ;
- state machine ;
- cache ;
- classification déterministe ;
- contexte de conversation.

Appeler Gemini uniquement lorsque cela apporte une vraie valeur.

Exemple :

"oui"

ne nécessite pas nécessairement un appel LLM.

"Je veux plutôt quelque chose à 150 000 vers Bastos ou Mvan"

peut nécessiter l'IA.

Le système doit donc utiliser une stratégie :

Deterministic processing
→ si confiance suffisante
→ action

sinon :

LLM
→ structured output
→ validation
→ action.

Objectif : réduire fortement le nombre de tokens consommés.

---

# 8. MACHINE À ÉTATS CONVERSATIONNELLE

Ne jamais laisser le LLM gérer seul l'état de la conversation.

Créer un véritable état métier.

Exemple :

LANDLORD_ONBOARDING

STATES :

START
COLLECT_PHONE
COLLECT_IDENTITY
COLLECT_CNI
COLLECT_SELFIE
VERIFYING
VERIFIED
CREATE_PROPERTY
COLLECT_PROPERTY_TYPE
COLLECT_LOCATION
COLLECT_PRICE
COLLECT_FEATURES
COLLECT_MEDIA
CONFIRM_PROPERTY
PUBLISHED

Même principe pour le locataire.

La conversation doit être reprise correctement même après :

- crash ;
- redémarrage ;
- timeout ;
- changement de worker ;
- perte temporaire de connexion.

L'état doit être persistant.

---

# 9. PARCOURS BAILLEUR

Le bailleur doit être davantage vérifié qu'un locataire.

Processus :

REGISTER
→ VERIFY PHONE
→ REQUEST LANDLORD
→ CNI
→ SELFIE
→ VERIFICATION
→ APPROVED
→ LANDLORD ACTIVE

Le système doit empêcher la publication avant vérification.

Un bailleur vérifié peut ensuite publier plusieurs biens.

---

# 10. PUBLICATION D'UN BIEN

Conversation guidée :

"Je veux louer mon appartement."

Le système collecte progressivement :

- type ;
- ville ;
- quartier ;
- localisation ;
- nombre de chambres ;
- salles de bain ;
- superficie ;
- équipements ;
- parking ;
- eau ;
- électricité ;
- internet ;
- sécurité ;
- prix ;
- charges ;
- caution ;
- durée minimale ;
- conditions ;
- photos ;
- vidéo ;
- disponibilité.

Avant publication :

résumé envoyé au bailleur :

"Voici les informations de votre bien..."

Le bailleur confirme.

Seulement après confirmation :

PROPERTY_PUBLISHED

---

# 11. CONTRÔLE DES INFORMATIONS MANQUANTES

C'est une fonctionnalité centrale.

Après publication, le système doit régulièrement détecter :

- informations manquantes ;
- informations contradictoires ;
- prix anormal ;
- localisation trop vague ;
- absence de photos ;
- photos incohérentes ;
- durée minimale non définie ;
- charges non précisées ;
- disponibilité inconnue ;
- conditions ambiguës.

Le système doit pouvoir demander au bailleur :

> "Il manque une information importante : les charges mensuelles sont-elles incluses dans le loyer ?"

ou :

> "Vous avez indiqué que le logement est disponible, mais aucune date de disponibilité n'est renseignée."

---

# 12. CONFIRMATION DE DISPONIBILITÉ

La disponibilité d'un logement ne doit jamais être considérée comme éternelle.

Le système doit régulièrement demander au bailleur :

> "Bonjour, votre appartement de Bastos est-il toujours disponible ?"

Le bailleur répond :

- Oui ;
- Non ;
- Loué ;
- Indisponible temporairement.

Si :

"loué"

alors :

PROPERTY → RENTED

Si :

"indisponible"

alors :

PROPERTY → UNAVAILABLE

Le bien doit immédiatement disparaître des résultats publics si nécessaire.

---

# 13. FEEDBACK APRÈS VISITE

Après une visite, le système doit contacter le locataire.

Questions possibles :

1. Avez-vous réellement visité le logement ?
2. Le logement correspondait-il aux photos ?
3. Le prix était-il celui annoncé ?
4. Les conditions étaient-elles celles annoncées ?
5. Le bailleur vous a-t-il demandé un paiement supplémentaire ?
6. Le logement était-il réellement disponible ?
7. Comment avez-vous trouvé cette annonce ?
8. Avez-vous finalement pris ce logement ?
9. Le bailleur a-t-il respecté les conditions annoncées ?
10. Souhaitez-vous signaler quelque chose ?

---

# 14. QUESTION CRITIQUE : SOURCE DE L'ANNONCE

Toujours demander au locataire :

> "Comment avez-vous trouvé ce logement ?"

Réponses possibles :

- WhatsApp ;
- plateforme ;
- Facebook ;
- TikTok ;
- Instagram ;
- ami ;
- famille ;
- agence ;
- bouche-à-oreille ;
- autre.

Cette donnée doit être stockée.

Elle permet de détecter :

- annonces copiées ;
- acquisition externe ;
- contournement de la plateforme ;
- faux locataires ;
- faux bailleurs ;
- campagnes marketing ;
- comportements anormaux.

---

# 15. DÉTECTION DU CONTOURNEMENT

Le système doit détecter lorsqu'un bailleur ou un locataire tente de contourner la plateforme.

Exemples :

- partage de numéro avant paiement ;
- partage de coordonnées externes ;
- tentative d'emmener la conversation hors plateforme ;
- demande de paiement direct ;
- publication d'un numéro dans une description ;
- messages suspects.

Le système peut détecter certains patterns automatiquement.

Cependant :

NE PAS bannir automatiquement sur un simple signal.

Créer un Risk Score.

---

# 16. FRAUD RISK ENGINE

Chaque utilisateur et chaque propriété peuvent avoir un score de risque.

Exemple :

risk_score = 0 → 100

Facteurs possibles :

- identité non vérifiée ;
- plusieurs comptes ;
- plusieurs numéros ;
- changements fréquents ;
- annonces supprimées ;
- annonces signalées ;
- prix anormalement bas ;
- même photo utilisée sur plusieurs biens ;
- mêmes coordonnées sur plusieurs comptes ;
- demandes de paiement suspectes ;
- comportement inhabituel ;
- nombreux refus ;
- nombreux signalements ;
- incohérences dans les informations ;
- changements fréquents de localisation ;
- tentative de contournement.

Créer des événements :

SuspiciousBehaviorDetected
FraudRiskScoreUpdated
PropertyReported
LandlordReported
TenantReported

---

# 17. PHOTO FRAUD

Prévoir une architecture permettant ultérieurement :

- perceptual hash ;
- détection de photos identiques ;
- détection de photos provenant d'autres annonces ;
- détection de duplications ;
- analyse de cohérence.

Exemple :

Un même appartement apparaît sous trois bailleurs différents.

Le système doit générer une alerte.

---

# 18. PRIX ANORMAUX

Détecter les annonces fortement éloignées du marché.

Exemple :

Un appartement normalement autour de 300 000 FCFA apparaît à 80 000 FCFA.

Ne pas bloquer automatiquement.

Créer :

PRICE_ANOMALY

Puis :

Risk Engine
→ score
→ éventuellement review humaine.

---

# 19. FEEDBACK APRÈS LOCATION

Après confirmation qu'un locataire a réellement pris le logement :

demander :

- Le logement correspondait-il à l'annonce ?
- Le prix était-il correct ?
- Les conditions étaient-elles respectées ?
- Le bailleur a-t-il respecté ses engagements ?
- Y avait-il des frais cachés ?
- Le logement était-il conforme aux photos ?
- Recommanderiez-vous ce bailleur ?

Cela crée un historique de confiance.

---

# 20. TRUST SCORE

Prévoir un Trust Score pour les bailleurs.

Ce score ne doit pas être une simple moyenne d'avis.

Il peut prendre en compte :

- ancienneté ;
- identité vérifiée ;
- nombre de locations réussies ;
- taux de conformité ;
- signalements ;
- annulations ;
- disponibilité correcte ;
- feedback des locataires ;
- respect des prix annoncés ;
- respect des conditions ;
- comportement.

Afficher seulement les informations utiles au locataire.

---

# 21. SYSTÈME DE SIGNALEMENT

Un locataire doit pouvoir dire :

> "Je veux signaler ce bailleur."

ou :

> "Cette annonce est fausse."

Types :

- fake_property ;
- fake_landlord ;
- wrong_price ;
- unavailable_property ;
- misleading_photos ;
- hidden_fee ;
- harassment ;
- scam ;
- duplicate_listing ;
- other.

Les signalements doivent être persistants et auditables.

---

# 22. AUDIT LOG

Chaque événement critique doit être traçable.

Exemples :

- landlord_verified ;
- property_created ;
- property_updated ;
- property_published ;
- property_unpublished ;
- property_marked_rented ;
- payment_completed ;
- phone_revealed ;
- fraud_detected ;
- account_suspended ;
- report_created.

Créer un audit log immuable.

---

# 23. PAIEMENT ET DÉBLOCAGE

Le locataire sélectionne un logement.

Avant de débloquer les coordonnées :

→ confirmation du numéro de paiement
→ paiement de 1 000 FCFA
→ confirmation du paiement
→ création d'un ServiceAccessGrant
→ déblocage des informations autorisées.

Ne jamais considérer :

payment initiated

comme :

payment completed.

Utiliser une vraie machine à états.

---

# 24. SÉCURITÉ

Prévoir :

- rate limiting ;
- validation stricte des entrées ;
- protection contre replay ;
- idempotency keys ;
- signatures webhook ;
- secrets dans variables d'environnement ;
- chiffrement des données sensibles ;
- chiffrement des documents sensibles ;
- contrôle d'accès ;
- RBAC ;
- audit log ;
- expiration des tokens ;
- rotation des credentials ;
- protection contre enumeration ;
- protection contre abuse.

Les CNI et selfies sont des données extrêmement sensibles.

Ne jamais les exposer directement via une URL publique.

---

# 25. STOCKAGE DES MÉDIAS

Ne pas stocker les images directement dans PostgreSQL.

Utiliser un object storage compatible S3.

PostgreSQL conserve uniquement :

- media_id ;
- object_key ;
- metadata ;
- checksum ;
- ownership ;
- status.

Les URLs doivent être signées et temporaires.

---

# 26. BASE DE DONNÉES

PostgreSQL doit être conçu pour le scale.

Prévoir :

- indexes ;
- composite indexes ;
- partial indexes ;
- unique constraints ;
- foreign keys pertinentes ;
- pagination cursor-based ;
- connection pooling.

Éviter :

OFFSET massif.

Préférer :

cursor pagination.

Toutes les requêtes critiques doivent être analysables avec EXPLAIN.

---

# 27. REDIS

Utiliser Redis pour :

- cache ;
- rate limiting ;
- distributed locks ;
- sessions temporaires ;
- idempotency ;
- conversation hot state ;
- counters ;
- throttling.

Ne jamais utiliser Redis comme source de vérité pour les données métier critiques.

---

# 28. RABBITMQ

RabbitMQ sert à découpler les traitements.

Exemples de queues :

whatsapp.incoming
whatsapp.outgoing
ai.requests
fraud.analysis
property.verification
notifications
payments.events
analytics.events

Les consumers doivent être idempotents.

Prévoir :

- retry ;
- dead-letter queue ;
- exponential backoff ;
- monitoring ;
- consumer concurrency.

---

# 29. OBSERVABILITÉ

Prévoir dès le début :

- structured logging ;
- correlation_id ;
- request_id ;
- user_id ;
- conversation_id ;
- property_id ;
- event_id.

Mesurer :

- latency ;
- throughput ;
- error rate ;
- queue depth ;
- consumer lag ;
- LLM latency ;
- LLM cost ;
- tokens ;
- WhatsApp failures ;
- database latency ;
- cache hit rate ;
- fraud detection rate.

Prévoir OpenTelemetry si possible.

---

# 30. COÛT IA

Le coût Gemini doit être surveillé.

Chaque appel IA doit pouvoir être associé à :

- user_id ;
- conversation_id ;
- use_case ;
- model ;
- input tokens ;
- output tokens ;
- latency ;
- estimated cost.

Créer un budget/usage tracker.

Le système doit éviter les appels IA inutiles.

---

# 31. CACHE

Exemples :

- propriétés populaires ;
- recherches fréquentes ;
- quartiers ;
- types de logements ;
- configuration ;
- réponses déterministes.

Attention à l'invalidation.

Ne jamais servir un logement loué comme disponible simplement parce qu'une ancienne valeur est encore en cache.

Les changements critiques doivent invalider les caches correspondants.

---

# 32. CONCURRENCE

Prévoir les scénarios :

Deux locataires sélectionnent le même logement.

Deux utilisateurs tentent de réserver.

Deux workers traitent le même message.

Deux événements de paiement arrivent.

Deux demandes modifient simultanément un bien.

Utiliser :

- transactions ;
- optimistic locking ;
- unique constraints ;
- idempotency ;
- locks distribués uniquement lorsqu'ils sont réellement nécessaires.

---

# 33. DOMAIN EVENTS

Créer des événements métier.

Exemples :

LandlordVerified
PropertyPublished
PropertyUpdated
PropertyMarkedUnavailable
PropertyRented
RentalApplicationCreated
VisitScheduled
VisitCompleted
PaymentCompleted
ContactInformationUnlocked
FraudRiskDetected
ReportCreated
TenantFeedbackSubmitted

Les événements doivent être indépendants des transports.

---

# 34. STRUCTURE DU PROJET

Proposer une structure claire proche de :

src/

  bounded_contexts/

    identity/
      domain/
      application/
      infrastructure/
      interfaces/

    landlord/
      domain/
      application/
      infrastructure/
      interfaces/

    property/
      domain/
      application/
      infrastructure/
      interfaces/

    rental/
      domain/
      application/
      infrastructure/
      interfaces/

    visit/
      domain/
      application/
      infrastructure/
      interfaces/

    payment/
      domain/
      application/
      infrastructure/
      interfaces/

    fraud/
      domain/
      application/
      infrastructure/
      interfaces/

    messaging/
      domain/
      application/
      infrastructure/
      interfaces/

    ai/
      domain/
      application/
      infrastructure/
      interfaces/

  shared/
    domain/
    application/
    infrastructure/

---

# 35. RÈGLES DDD

Les entités doivent protéger leurs invariants.

Ne pas créer des modèles anémiques contenant seulement des attributs.

Exemple :

Property ne doit pas permettre :

property.mark_as_rented()

si son état ne permet pas cette transition.

Même chose pour :

Landlord
Payment
RentalApplication
Visit
Verification.

Les Value Objects doivent être utilisés lorsqu'ils apportent une vraie valeur.

Exemples :

Money
PhoneNumber
PropertyId
UserId
Coordinates
Address
VerificationId

---

# 36. SOLID

Respecter :

S — une responsabilité claire ;
O — extension sans modification inutile ;
L — substituabilité ;
I — interfaces petites ;
D — dépendance vers abstractions.

Exemple :

class AIProvider(Protocol)

class PaymentGateway(Protocol)

class PropertyRepository(Protocol)

class MessageSender(Protocol)

Le domaine ne doit jamais dépendre de leurs implémentations.

---

# 37. TESTS

Prévoir :

### Unit tests

Domain entities
Value objects
Aggregates
Domain services
State transitions
Fraud rules

### Integration tests

PostgreSQL
Redis
RabbitMQ
OpenRouter adapter
Baileys adapter

### Contract tests

AI structured output
Payment provider
WhatsApp adapter

### End-to-end

Tenant onboarding
Landlord onboarding
Property creation
Property search
Visit
Payment
Unlock contact
Fraud report

---

# 38. TESTS DE CHARGE

La plateforme doit être testée avant production.

Tester notamment :

- messages WhatsApp entrants ;
- recherches ;
- création de biens ;
- consultations ;
- paiements ;
- événements RabbitMQ.

Utiliser un outil de load testing adapté.

Mesurer :

- p50 ;
- p95 ;
- p99 ;
- throughput ;
- CPU ;
- RAM ;
- PostgreSQL connections ;
- Redis memory ;
- RabbitMQ queue depth.

L'objectif est de connaître le véritable point de saturation.

---

# 39. RÉSILIENCE

Si Gemini tombe :

la plateforme doit continuer à fonctionner autant que possible.

Si Redis tombe :

les données critiques doivent rester disponibles.

Si RabbitMQ est temporairement indisponible :

les opérations critiques ne doivent pas être silencieusement perdues.

Si WhatsApp est temporairement indisponible :

les événements sortants doivent être retryés.

Si PostgreSQL est indisponible :

fail fast proprement.

Prévoir timeouts partout.

Ne jamais attendre indéfiniment un service externe.

---

# 40. ADMIN / MODÉRATION

Prévoir une interface administrative permettant de :

- voir les utilisateurs ;
- voir les bailleurs ;
- voir les vérifications ;
- voir les propriétés ;
- voir les signalements ;
- voir les scores de risque ;
- suspendre un compte ;
- suspendre un bien ;
- consulter l'audit ;
- voir les transactions ;
- voir les événements suspects.

---

# 41. ANTI-FRAUDE : APPROCHE HYBRIDE

Ne pas dépendre uniquement d'un LLM.

Combiner :

### Rules engine

Détection déterministe.

### Behavioral analysis

Analyse des comportements.

### Statistical anomalies

Détection des comportements inhabituels.

### LLM

Analyse sémantique des conversations lorsque nécessaire.

### Human review

Pour les cas à risque élevé.

Le système doit produire :

risk_score
risk_reasons
recommended_action

Exemple :

{
  "risk_score": 87,
  "reasons": [
    "PROPERTY_DUPLICATE",
    "PRICE_ANOMALY",
    "PHONE_REUSE",
    "PAYMENT_OFF_PLATFORM_ATTEMPT"
  ],
  "recommended_action": "MANUAL_REVIEW"
}

---

# 42. NE PAS SUR-AUTOMATISER LES BANS

Un score élevé ne doit pas nécessairement provoquer immédiatement un bannissement.

Prévoir :

LOW_RISK
→ normal

MEDIUM_RISK
→ additional verification

HIGH_RISK
→ manual review

CRITICAL
→ temporary restriction

Cela limite les faux positifs.

---

# 43. MÉTRIQUES BUSINESS

Le système doit également mesurer :

- nombre de bailleurs ;
- bailleurs vérifiés ;
- nombre de biens ;
- biens disponibles ;
- biens loués ;
- recherches ;
- visites ;
- locations réussies ;
- conversion ;
- paiement service fee ;
- taux de signalement ;
- taux de fraude ;
- taux de disponibilité incorrecte ;
- taux d'informations incorrectes ;
- taux de satisfaction.

---

# 44. QUESTIONNAIRE AUTOMATIQUE

Après les étapes importantes, l'assistant peut déclencher des questions de contrôle.

Après publication :

> "Votre logement est-il toujours disponible ?"

Après visite :

> "Le logement correspondait-il à l'annonce ?"

Après location :

> "Avez-vous finalement pris ce logement ?"

Si NON :

> "Pourquoi ?"

Si problème :

> "Pouvez-vous nous préciser ce qui ne correspondait pas ?"

Ces réponses alimentent le système de confiance et antifraude.

---

# 45. OBJECTIF PRODUIT

Le système doit créer une boucle de confiance :

Bailleur vérifié
↓
Bien vérifié
↓
Annonce contrôlée
↓
Locataire intéressé
↓
Visite
↓
Feedback
↓
Location
↓
Confirmation
↓
Trust Score
↓
Meilleure confiance future

Plus la plateforme est utilisée, plus elle doit devenir capable de détecter les comportements suspects.

---

# 46. RÈGLE ABSOLUE

Ne jamais sacrifier la qualité du domaine pour aller plus vite.

Ne jamais mettre toute la logique dans :

- FastAPI routes ;
- handlers WhatsApp ;
- services gigantesques ;
- prompts LLM ;
- modèles SQLAlchemy.

Les controllers doivent être minces.

Les use cases orchestrent.

Le domaine contient les règles métier.

L'infrastructure implémente les dépendances externes.

---

# 47. APPROCHE DE DÉVELOPPEMENT

Ne pas essayer de développer toute la plateforme en une seule fois.

Construire progressivement.

### Phase 1

Foundation :

- project structure ;
- DDD ;
- PostgreSQL ;
- migrations ;
- configuration ;
- logging ;
- Docker ;
- tests ;
- health checks.

### Phase 2

Identity :

- user ;
- tenant ;
- landlord ;
- phone verification.

### Phase 3

Landlord verification :

- CNI ;
- selfie ;
- verification workflow.

### Phase 4

Property :

- create ;
- update ;
- publish ;
- availability ;
- search.

### Phase 5

WhatsApp :

- Baileys ;
- incoming messages ;
- outgoing messages ;
- conversation state.

### Phase 6

AI :

- OpenRouter ;
- Gemini ;
- intent detection ;
- entity extraction ;
- response generation.

### Phase 7

Rental:

- interest ;
- negotiation ;
- visit ;
- rental confirmation.

### Phase 8

Payment :

- 1 000 FCFA ;
- payment state machine ;
- contact unlock.

### Phase 9

Fraud :

- rules ;
- risk score ;
- reports ;
- behavioral analysis.

### Phase 10

Scale :

- Redis ;
- RabbitMQ ;
- workers ;
- horizontal scaling ;
- load tests ;
- observability.

---

# 48. CONSIGNE FINALE POUR L'IA DE DÉVELOPPEMENT

Avant d'écrire du code :

1. analyser les bounded contexts ;
2. identifier les aggregates ;
3. identifier les entities ;
4. identifier les value objects ;
5. identifier les domain services ;
6. identifier les domain events ;
7. identifier les repositories ;
8. identifier les interfaces externes ;
9. identifier les invariants ;
10. identifier les scénarios concurrents ;
11. identifier les risques de performance ;
12. identifier les risques de fraude.

Puis proposer une architecture.

Ensuite seulement commencer l'implémentation.

À chaque étape :

- écrire les tests ;
- implémenter le domaine ;
- implémenter les use cases ;
- implémenter les adapters ;
- intégrer ;
- tester ;
- mesurer.

Ne jamais créer de code inutile.

Ne jamais ajouter une technologie simplement parce qu'elle est populaire.

Chaque composant doit avoir une justification architecturale.

La priorité est :

**correctness → security → domain integrity → performance → scalability → cost efficiency → developer experience.**

La plateforme doit être capable de commencer raisonnablement petite tout en permettant une évolution progressive vers plusieurs millions d'utilisateurs sans devoir réécrire complètement le cœur métier.