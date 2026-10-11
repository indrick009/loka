## KORA FOOD — VISION ET FLUX MÉTIER (V1)

> Mis à jour le 11 octobre 2026 : fermeture du menu du jour décidée par le
> restaurateur, avec expiration automatique à minuit (§4.4).

### 1. Le problème

Au Cameroun, une grande partie de la nourriture est vendue par des restaurants et vendeurs de quartier dont le menu change d'un jour à l'autre. Pour savoir ce qui est disponible aujourd'hui près de soi, il faut se déplacer ou appeler.

### 2. La solution Kora

Kora permet :

- au **restaurateur** de publier son **menu du jour** ;
- au **client** de trouver, à partir d'une simple conversation, un plat disponible aujourd'hui près de lui ou correspondant à ce qu'il a envie de manger ;
- à Kora d'**envoyer la demande au restaurateur** pour réserver le plat.

### 3. Principe technique

Le même principe que pour Housing et Dating s'applique :

- **Le LLM gère la conversation.** Il pose les questions, comprend les réponses et reformule.
- **Le backend reçoit des actions structurées** produites par le LLM (les noms définitifs restent à concevoir).
- L'utilisateur peut donner plusieurs informations dans un seul message. Kora enregistre ce qui a été donné et **ne demande que ce qui manque**.

### 4. Parcours restaurateur : publier le menu du jour

#### 4.1 Enregistrement du restaurant

| # | Information | Règles |
| --- | --- | --- |
| 1 | Nom du restaurant | |
| 2 | Localisation | Comme pour Housing : Kora suggère d'abord de partager la localisation WhatsApp. Sinon, il demande la ville et le quartier, les compare à sa liste pour corriger les fautes et demande confirmation si nécessaire. |

#### 4.2 Menus récurrents

Le restaurateur peut indiquer les **menus qui reviennent souvent au cours de la semaine**. Ils servent de base pour composer plus vite le menu du jour.

#### 4.3 Menu du jour

**C'est le menu du jour qui compte pour Kora.**

| # | Information | Règles |
| --- | --- | --- |
| 1 | Plats disponibles aujourd'hui | Le restaurateur peut les écrire librement ou les choisir parmi ses menus récurrents. Kora comprend les noms des plats locaux (ndolè, poulet DG, soya, poisson braisé, etc.). |
| 2 | Prix de chaque plat | Kora interprète les montants selon le contexte camerounais, comme pour Housing (« 2 mil » = 2 000 FCFA). |

Une fois le menu du jour soumis, il devient visible pour les recherches des clients.

#### 4.4 Fermeture du menu du jour

**C'est le restaurateur qui décide**, exactement comme le bailleur décide de la
disponibilité de son logement. Il peut écrire à tout moment :

| Ce qu'il écrit | Effet |
| --- | --- |
| « le ndolè est terminé » | Ce plat seul sort du menu du jour ; les autres restent |
| « c'est fini pour aujourd'hui » | Tout le menu du jour sort des recherches |

**Garde-fou : le menu du jour expire automatiquement à minuit.** Un restaurateur
qui oublie de fermer n'envoie donc pas de clients le lendemain vers le menu de
la veille — le dégât d'un oubli reste borné à la journée.

Kora ne propose jamais un plat retiré par le restaurateur ni un menu expiré.

### 5. Parcours client : chercher de la nourriture

Le client peut chercher de deux façons, éventuellement combinées :

| Type de demande | Exemple | Ce que Kora utilise |
| --- | --- | --- |
| Par proximité | « Je cherche la nourriture auprès de moi » | La position du client. Comme pour Housing, Kora lui conseille de partager sa localisation WhatsApp ; sinon, il demande la ville et le quartier. |
| Par type de nourriture | « Je cherche du poulet braisé » | Le plat ou le type de plat demandé. |

### 6. Correspondance

Kora cherche, **dans les menus du jour publiés**, les plats qui correspondent à la demande du client (proximité, type de nourriture, ou les deux).

Kora ne propose jamais un plat qui ne figure pas dans un menu du jour publié.

### 7. Envoi de la demande au restaurateur

En fonction de la correspondance, Kora **envoie la demande au propriétaire du restaurant** pour réserver le plat du client.

**C'est le restaurant qui rappelle le client, pas Kora qui relaie.** Au moment où
la demande part, Kora écrit au client :

> « Le restaurant vous contactera d'ici peu. Si ce n'est pas le cas, merci de
> venir nous le dire. »

Kora ne dit donc **jamais** « votre plat est réservé » : elle ne le sait pas.
Elle annonce ce qu'elle a fait, et ce que le client doit attendre.

La boucle de retour est humaine, pas automatique : si le restaurant ne rappelle
pas, c'est **le client qui le signale** à Kora. Ce signalement relève de la
section Avis et réclamations (§11), et c'est lui qui permet de repérer un
restaurant qui ne répond jamais.

**Ce que Kora transmet, et ce qu'elle en dit.** Tranché le 11 octobre : Kora
transmet au restaurant le **numéro WhatsApp et le prénom** du client, et elle
l'annonce au client au moment où la demande part :

> « Le restaurant va vous contacter sur ce numéro. »

C'est le **seul endroit de Kora où un contact est transmis sans que la personne
concernée ait payé ou débloqué quoi que ce soit** — partout ailleurs, celui qui
paie reçoit un contact, jamais l'inverse. D'où l'obligation de le dire : la
personne doit savoir ce qui est partagé et avec qui, ce qu'exige la loi
camerounaise n° 2024/017 sur la protection des données personnelles.

C'est aussi ce qui rend la phrase précédente intelligible : un client qui sait
que le restaurant a son numéro comprend pourquoi on lui demande de signaler
l'absence d'appel.

### 8. Paiement

**Le restaurant paie 2 000 FCFA par semaine.** C'est un **abonnement du
provider**, et non un déblocage payé par le consommateur comme dans Housing,
Dating et Found.

Le client, lui, ne paie rien : ni recherche, ni demande de réservation.

#### Quand l'abonnement n'est pas payé

Tranché le 11 octobre : **délai de grâce, puis retrait des recherches.** Quelques
jours de tolérance après l'échéance, et passé ce délai le restaurant sort des
recherches jusqu'à ce qu'il paie. Il n'est rien détruit : il revient en payant.

Pourquoi pas un retrait immédiat : sur un paiement mobile hebdomadaire de
2 000 FCFA, l'oubli d'un jour sera fréquent, et faire perdre à un restaurateur
ses clients du jour pour un retard d'une matinée le ferait partir. Pourquoi pas
non plus une simple relance : sans effet concret, l'abonnement devient optionnel
de fait et Kora travaille gratuitement.

**Durée du délai de grâce : à fixer.** C'est du réglage, pas de la conception.

#### Deux horloges, à ne pas confondre

| Horloge | Ce qu'elle gouverne | Qui la déclenche |
| --- | --- | --- |
| Abonnement hebdomadaire | le **droit de publier** un menu | le paiement, ou son absence |
| Expiration à minuit (§4.4) | la **validité du menu du jour** | le temps, ou le restaurateur |

Un abonnement à jour ne rend pas visible le menu de la veille, et un menu publié
aujourd'hui ne survit pas à un abonnement échu. Les deux conditions doivent être
vraies pour qu'un plat apparaisse dans une recherche.

### 9. Périmètre

Ce flux constitue la **V1** de Kora Food.

---

### 10. Avis et plaintes

Transverse à tous les services, spécifié à part :
[`kora-avis-plaintes-v1.md`](./kora-avis-plaintes-v1.md).

Le signalement de la §7 — un restaurant qui ne rappelle jamais — est une
**plainte** au sens de ce document.
