## KORA FOUND — VISION ET FLUX MÉTIER (V1)

> Les sections 1 à 6 décrivent la vision du produit. Les règles marquées
> **[Sécurité]** ont été ajoutées pour limiter les risques, et sont
> **validées le 11 octobre 2026** : ce sont des règles V1 à part entière, pas
> des propositions. Chacune reste rouvrable, mais par défaut on part protégé.

### 1. Le problème

Au Cameroun, perdre sa carte nationale d'identité (CNI) ou un autre document est un vrai casse-tête. Une personne qui retrouve un document ne sait pas comment joindre son propriétaire, et le propriétaire ne sait pas où chercher.

### 2. La solution Kora

Kora met en relation **la personne qui a trouvé un document** (le trouveur) et **la personne qui l'a perdu** (le propriétaire) :

- le trouveur décrit complètement le document et envoie des photos ;
- le propriétaire doit **prouver que le document lui appartient** avant toute restitution.

Les documents concernés : CNI, passeport, permis de conduire, carte d'étudiant, et autres documents ou objets.

### 3. Principe technique

Le même principe que pour Housing, Dating et Food s'applique :

- **Le LLM gère la conversation.** Il pose les questions, comprend les réponses et reformule.
- **Le backend reçoit des actions structurées** produites par le LLM et effectue la correspondance et les vérifications.
- L'utilisateur peut donner plusieurs informations dans un seul message. Kora enregistre ce qui a été donné et **ne demande que ce qui manque**.

### 4. Parcours trouveur : déclarer un document trouvé

| # | Information | Règles |
| --- | --- | --- |
| 1 | Type de document | CNI, passeport, permis, carte d'étudiant, autre. |
| 2 | Lieu où il a été trouvé | Partage de la localisation WhatsApp ou ville et quartier, vérifiés comme pour Housing. |
| 3 | Date à laquelle il a été trouvé | |
| 4 | Description complète | Le trouveur donne tous les détails possibles. |
| 5 | Photos | Le trouveur envoie les photos du document. |

**[Sécurité]** Avant l'envoi des photos, Kora explique au trouveur que les photos ne seront **jamais publiées** ni montrées aux personnes qui cherchent : elles servent uniquement à la vérification.

### 5. Parcours propriétaire : chercher un document perdu

| # | Information | Règles |
| --- | --- | --- |
| 1 | Type de document | |
| 2 | Nom complet figurant sur le document | |
| 3 | Lieu et date approximatifs de la perte | |
| 4 | Description | |
| 5 | Preuves de propriété | Éléments qui prouvent que le document lui appartient (section 7). |

### 6. Correspondance

Le backend compare les déclarations des trouveurs et les demandes des propriétaires (type, nom, lieu, date, description).

**[Sécurité]** Kora ne confirme jamais à un demandeur qu'un document **au nom d'une personne précise** a été trouvé tant que la preuve de propriété n'est pas validée. Il peut seulement indiquer qu'une correspondance possible existe et demander les preuves.

### 7. Vérification de la propriété

Le propriétaire doit fournir des éléments qui prouvent que le document est à lui. Exemples possibles :

- une photocopie ou une photo ancienne du document ;
- l'attestation de déclaration de perte délivrée par le commissariat ou la gendarmerie ;
- le récépissé de demande de CNI ;
- un autre document à son nom (acte de naissance, carte d'étudiant) ;
- des informations figurant sur le document que seul le propriétaire connaît (numéro du document, date et lieu de naissance).

**[Sécurité]**
- Kora compare ces éléments avec les photos du trouveur **sans jamais montrer les photos du trouveur au demandeur**.
- Le demandeur doit fournir l'information ; Kora ne la lui suggère jamais (pas de question du type « Êtes-vous né le 12 mars ? »).
- Une photo du visage du demandeur peut être comparée à la photo figurant sur le document.
- En V1, la validation finale est faite par **une personne de l'équipe Kora** avant toute mise en relation.

### 8. Paiement et déblocage de la mise en relation

Une fois la propriété validée, comme pour Housing :

1. Le propriétaire paie **1 000 FCFA** à Kora pour débloquer la mise en relation avec le trouveur.
2. Le paiement n'est demandé **qu'après la validation de la preuve de propriété**, jamais avant.
3. Kora demande : « Voulez-vous procéder au paiement ? »
4. Si oui, Kora propose de payer avec un numéro donné ; le propriétaire peut le changer.
5. Dès que le propriétaire valide, Kora lance la demande de retrait ; il la valide sur son téléphone.
6. Le paiement passe par notre service de paiement, via un compte **Fapshi**.

### 9. Mise en relation et restitution

Une fois le paiement effectué, Kora met en relation le trouveur et le propriétaire.

**[Sécurité]**
- La restitution se fait dans **un lieu public** ou dans **un commissariat ou une gendarmerie**. Kora le recommande explicitement aux deux parties.
- Le trouveur peut aussi choisir de déposer directement le document au commissariat ; Kora indique alors au propriétaire où le récupérer.
- **Aucun paiement ne conditionne la restitution entre le trouveur et le propriétaire.** Les 1 000 FCFA payés à Kora sont les seuls frais. Toute demande d'argent de la part du trouveur est signalée comme un risque d'arnaque.

### 10. Risques et protections [Sécurité]

| Risque | Protection |
| --- | --- |
| Usurpation d'identité : un fraudeur se fait passer pour le propriétaire et récupère la CNI | Preuve de propriété obligatoire, validation par l'équipe, aucune information du document révélée avant validation. |
| Recherche « à la pêche » : quelqu'un tape des noms pour savoir quels documents ont été trouvés | Pas de confirmation nominative avant preuve, nombre de recherches limité par utilisateur. |
| Rançon : le trouveur exige de l'argent | Seuls les 1 000 FCFA payés à Kora sont dus ; restitution gratuite entre les parties, signalement, suspension du compte. |
| Document volé déclaré comme « trouvé » | Numéro WhatsApp du trouveur conservé, restitution recommandée via commissariat, signalement possible. |
| Fuite des photos de documents | Stockage privé et chiffré, accès réservé au backend et à l'équipe de vérification, aucune photo dans les journaux, suppression après restitution ou après un délai fixé. |
| Agression lors du rendez-vous | Lieu public ou poste de police recommandé. |
| Données personnelles | Collecte limitée au nécessaire, conformément à la loi n° 2024/017 sur la protection des données personnelles. |

### 11. Périmètre

Ce flux constitue la **V1** de Kora Found. Kora Found reste un domaine de **priorité 2** : il ne doit pas retarder Housing, Food et Dating.
