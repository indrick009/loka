## KORA — AVIS ET PLAINTES (V1)

> Ajouté le 11 octobre 2026. **Transverse** à Housing, Food, Dating et Found.

### 1. Pourquoi

Tout service où Kora met deux personnes en relation peut mal tourner, et Kora
n'a aujourd'hui aucun moyen de l'apprendre. Elle quitte la relation au moment
même de la mise en relation : sans retour des utilisateurs, elle ne saura jamais
qu'un bailleur est injoignable ou qu'un restaurant ne rappelle jamais.

### 2. Deux choses de nature différente

|  | Sert à | Porte sur |
| --- | --- | --- |
| **Plainte** | savoir quel service ou quel provider **sanctionner ou bannir** | un provider précis |
| **Avis** | savoir lesquels **mettre en avant** | une offre et son provider |

Une plainte est punitive, un avis est valorisant. Les confondre ferait d'une
mauvaise note un motif de bannissement, et d'une plainte un simple signal de
classement.

### 3. Le provider

Le provider est la personne qui offre le service : bailleur, restaurateur,
trouveur, profil de rencontre.

**La sanction porte sur le provider, pas sur l'offre.** Sinon un bailleur banni
republie son logement sous une nouvelle annonce et rien n'a été réglé.

### 4. Usages identifiés, par domaine

| Domaine | Plainte typique |
| --- | --- |
| Housing | bailleur injoignable après paiement, logement déjà loué |
| Food | restaurant qui ne rappelle jamais (§7 de la spec Food) |
| Found | trouveur qui réclame de l'argent (§10 de la spec Found) |
| Dating | contenu ou comportement inapproprié |

### 5. Les avis ne sont pas publics

Tranché le 11 octobre : **un avis est un signal interne de classement, rien de
plus.** Kora s'en sert pour ordonner ce qu'elle propose — un bailleur bien noté
remonte, un restaurant qui ne rappelle jamais descend. Rien n'est affiché aux
autres utilisateurs.

Raison décisive : à 1 000 utilisateurs par jour, un logement aura deux ou trois
avis. C'est trop peu pour qu'une note affichée veuille dire quoi que ce soit, et
largement assez pour trier.

Ce que cette décision évite : la modération de texte public, la défense contre
les faux avis, et les litiges avec un provider qui contesterait une note alors
que Kora n'a été témoin de rien — elle quitte la relation au moment de la mise
en relation.

**Conséquence sur la forme :** un avis interne n'a besoin que d'une **note** pour
classer. Le texte libre n'a d'utilité que sur une **plainte**, parce qu'un humain
va la lire pour décider d'une sanction.

### 6. La sanction est décidée par un humain

Tranché le 11 octobre : **Kora ne bannit jamais seule.** Les plaintes arrivent
dans une file, quelqu'un de l'équipe les lit et tranche. Même principe que la
vérification de propriété dans Found, où la validation finale est déjà humaine
en V1.

Un garde-fou automatique est admis : au bout de **N plaintes**, Kora peut
**masquer** un provider en attendant la décision. Masquer n'est pas bannir, et
c'est réversible sans dommage.

Pourquoi pas un seuil automatique de bannissement : bannir un bailleur, c'est
lui couper un revenu sur la foi de plaintes que Kora n'a pas pu vérifier — elle
n'était pas là. Un concurrent ou un locataire de mauvaise foi ferait tomber un
bailleur honnête avec quelques plaintes, et personne ne le rattraperait.

Deux valeurs à fixer, qui sont du réglage et non de la conception : **N**, et le
délai de traitement de la file. Une file humaine sans délai annoncé est une file
qui grossit en silence.

### 7. Qui peut déposer

**C'est la mise en relation qui donne la légitimité, pas le paiement.** Le
paiement ne peut pas servir de critère, parce qu'il ne désigne pas la même
personne selon le domaine : dans Food c'est le **restaurant** qui paie son
abonnement, et c'est le **client** qui se plaint.

| Domaine | Ce qui ouvre le droit de déposer |
| --- | --- |
| Housing | le paiement du déblocage, par le locataire |
| Found | le paiement du déblocage, par le propriétaire du document |
| Dating | le déblocage du contact — **des deux côtés**, voir ci-dessous |
| Food | la demande de réservation envoyée au restaurant |

Cas réels donnés par le propriétaire du produit :

| Domaine | Plainte |
| --- | --- |
| Housing | « le bailleur avait dit avec eau, il n'y a pas l'eau » ; « les photos ne correspondent pas » |
| Dating | « elle a menti, photo générée par IA » |
| Food | « on commande, elle ne vient pas » |

Ce sont tous des cas où la personne a **découvert après coup** que l'offre ne
correspondait pas. C'est exactement ce que Kora ne peut pas vérifier elle-même,
et donc ce que seules les plaintes peuvent lui apprendre.

**Dating, cas particulier à ne pas manquer :** celui qui a été débloqué n'a rien
payé, mais c'est lui qui doit pouvoir se plaindre d'un harcèlement. Le droit de
déposer ne peut donc pas suivre le paiement, même dans un domaine qui en a un.
