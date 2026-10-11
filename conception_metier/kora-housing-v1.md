## KORA HOUSING — VISION ET FLUX MÉTIER (V1)

### 1. Le problème

Au Cameroun, louer une chambre, un studio ou un appartement est très pénible :

1. Le locataire cherche sur Facebook et écrit à un agent immobilier.
2. L'agent l'ajoute à son groupe WhatsApp ou lui envoie des logements, souvent hors critères ou déjà indisponibles.
3. Pour visiter, le locataire paie environ 5 000 FCFA de frais de visite.
4. Si le logement lui convient, il paie un mois de loyer de commission à l'agent, avant même de payer le bailleur.

### 2. La solution Kora

Kora n'est **pas** une plateforme complexe de location immobilière.

Kora met **directement en relation le bailleur et le locataire**, sans agent immobilier, pour des frais de **1 000 FCFA** payés par le locataire.

### 3. Principe technique

- **Le LLM gère la conversation.** Il pose les questions, comprend les réponses et reformule.
- **Le backend reçoit des actions structurées** produites par le LLM, par exemple `userWantRent(price, location, …)` (exemple indicatif, les noms définitifs restent à concevoir).
- L'ordre des questions est fixé, mais l'utilisateur peut répondre à plusieurs questions dans un seul message. Le système enregistre tout ce qui a été donné et **ne pose que les questions complémentaires**.

### 4. Parcours bailleur : publier un logement

Le bailleur peut avoir un ou plusieurs logements à louer. Il commence la conversation et dit ce qu'il veut faire. Kora collecte les informations dans cet ordre :

| # | Information | Règles |
| --- | --- | --- |
| 1 | Type de logement | Chambre, studio, appartement ou maison. |
| 2 | Moderne ou non | Oui / non. |
| 3 | Plusieurs logements ? | Voir la section 5. |
| 4 | Localisation | **Très important.** Kora suggère d'abord de partager la localisation WhatsApp, pour localiser le logement de façon fiable et mieux orienter les locataires. Si le bailleur ne veut pas ou ne sait pas le faire, Kora demande la ville et le quartier. Kora dispose de la liste des villes et quartiers pour corriger les fautes de saisie, et demande confirmation si nécessaire. **Cette étape doit être validée avant de passer à la suite.** |
| 5 | Prix du loyer | Le bailleur peut écrire « 60mil », « 50mil », « 60k » ou « 50000 ». Kora interprète selon le contexte immobilier camerounais : « 50mil » signifie 50 000 FCFA, pas 50 millions (il n'existe pratiquement pas de studio à 50 millions). |
| 6 | Électricité et eau | Incluses dans le loyer ou non. Certains logements n'ont pas d'eau : la question et la réponse doivent être **très claires** pour bien comprendre la situation. |
| 7 | Caution | Y a-t-il une caution ? Si oui, combien de mois. |
| 8 | Sécurité | Gardien, parking. Point subtil : ces équipements concernent généralement les loyers à partir de 70 000 FCFA. |
| 9 | Photos | **Très important.** Dernière étape. |

Une fois les informations collectées, Kora publie le ou les logements.

### 5. Bailleur avec plusieurs logements

| Situation | Traitement |
| --- | --- |
| Types différents (ex. 2 studios et 1 chambre) | Collecter les informations de la chambre à part et celles du studio à part. |
| Même type, mêmes caractéristiques (ex. 2 studios identiques) | Un seul logement publié avec **quantité = 2**. |
| Même type, prix différents (ex. un studio à 60 000 et un studio à 70 000) | **Deux logements distincts.** |

### 6. Logements meublés

Une chambre, un studio ou un appartement peut être **meublé**. Dans ce cas, il se loue **généralement à la journée**.

La majorité du processus reste la même que pour un logement non meublé (parcours bailleur, parcours locataire, correspondance, vérification de disponibilité et paiement).

### 7. Parcours locataire : chercher un logement

Le locataire arrive avec une demande, par exemple « je cherche un logement ». Kora demande :

| # | Information | Règles |
| --- | --- | --- |
| 1 | Type de logement | Chambre, studio, appartement ou maison. |
| 2 | Montant maximum | Le loyer maximum qu'il accepte pour ce logement. |
| 3 | Nombre de mois disponibles | Combien de mois de loyer il peut payer d'avance. |
| 4 | Zone recherchée | Le locataire peut partager sa position ou saisir la localisation souhaitée. Kora lui conseille de partager sa position **quand il se trouve dans la zone où il cherche** (lieu de travail ou autre). |

C'est tout ce que Kora demande au locataire. **Le reste, c'est au système de le déduire** pour lui trouver un logement correspondant (match).

### 8. Correspondance et négociation

Deux cas se présentent :

- **Match direct** : le logement correspond aux critères du locataire.
- **Écart** : par exemple, le loyer est de 55 000 FCFA et le locataire veut 50 000 ; ou le bailleur demande un an d'avance et le locataire a 5 mois ; ou les deux à la fois.

En cas d'écart, Kora peut aller voir le bailleur pour négocier, **uniquement si son logement reçoit peu de demandes** :

| Situation après publication | Décision |
| --- | --- |
| Des personnes sont intéressées au prix affiché et personne ne négocie | Le prix est abordable : **pas de négociation**. |
| Beaucoup de personnes négocient | Kora propose au bailleur **les meilleures offres**, sans se répéter. |
| Personne ne demande le logement | Kora peut aller voir le bailleur. |

Déroulement de la négociation :

1. Kora demande au bailleur : « Pouvez-vous envisager telle ou telle condition ? »
2. Si le bailleur accepte, Kora revient vers le locataire et lui confirme que c'est d'accord.
3. Si le bailleur fait une contre-offre, Kora la transmet au locataire, **et s'arrête là**. Kora ne fait pas d'allers-retours répétés entre les deux parties.

### 9. Paiement et déblocage du contact

Une fois le logement confirmé :

1. **Avant tout paiement, Kora demande toujours au bailleur si le logement est encore disponible.** Le locataire ne paie que si le bailleur confirme la disponibilité.
2. Le locataire paie **1 000 FCFA** pour débloquer les informations du bailleur (son **numéro**) et la **localisation** du logement.
3. Kora demande : « Voulez-vous procéder au paiement ? »
4. Si oui, Kora propose de payer avec un numéro donné ; le locataire peut le changer.
5. Dès que le locataire valide, Kora lance la demande de retrait ; le locataire la valide sur son téléphone.
6. Le paiement passe par notre service de paiement, via un compte **Fapshi**.

### 10. Périmètre

Ce flux constitue la **V1** de Kora Housing. Rien d'autre n'est à ajouter côté métier à ce stade.
