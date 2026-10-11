## KORA FOOD — VISION ET FLUX MÉTIER (V1)

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

### 8. Périmètre

Ce flux constitue la **V1** de Kora Food.
