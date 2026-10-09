# Connaissance du marché immobilier camerounais

Ce document décrit la base de connaissance codée dans
`src/loka/bounded_contexts/ai/domain/knowledge/market.py` et explique **ce qui est
certain, ce qui est une habitude observée, ce qui varie et ce qui reste à
confirmer**. La règle fondatrice : le LLM n'est jamais la source de vérité. Ces
données, elles, le sont — et elles sont testées.

## Vocabulaire de confiance

Chaque entrée porte un niveau de confiance :

- `VERIFIED` — adossé à un texte de loi ou une source officielle.
- `OBSERVED` — rapporté de façon convergente par plusieurs annonces / guides.
- `TO_CONFIRM` — plausible mais issu d'une source unique ou secondaire ; sert
  d'indice, jamais de règle dure.

## Sources

| Clé | Source |
| --- | --- |
| `loi-2014-023` | Loi n°2014/023 du 24 décembre 2014 régissant les baux à usage d'habitation |
| `loi-2009-010` | Loi n°2009/010 du 10 juillet 2009 relative à la location-accession |
| `njikam` | Njikam.com — *Louer un appartement à Douala* (2025) |
| `onviiit` | Onviiit — *Le contrat de bail au Cameroun* |
| `lanation` | La Nation d'Afrique — *Quartiers les plus chers de Douala et Yaoundé* |
| `apparts` | Apparts-Meubles — *Top quartiers pour vivre à Yaoundé et Douala* |

## Pourquoi un quartier n'est pas une ville

Au Cameroun, la réponse naturelle à « où est le logement ? » est le quartier
(« Bastos », « Bonapriso »), pas la ville. Sans index quartier → ville, une
réponse parfaitement valide est inutilisable : le formulaire redemande la
localisation indéfiniment. L'index `NEIGHBOURHOODS` résout ce problème **sans
jamais deviner** : un lieu inconnu reste un indice, il n'est pas promu en ville.

Villes couvertes : Douala, Yaoundé, Buea, Bafoussam (plus les alias de villes
Kribi, Limbé, Garoua, Ngaoundéré, Bamenda, Maroua, Bertoua, Ebolowa, Kumba,
Dschang).

## Fourchettes de loyer (XAF / mois)

Ce sont des **ancrages de magnitude**, jamais des règles de rejet. Une fourchette
large évite que le modèle lise « 50 » comme cinquante francs.

| Type | Basse | Haute | Confiance |
| --- | --- | --- | --- |
| Chambre (ROOM) | 10 000 | 60 000 | OBSERVED |
| Studio | 20 000 | 150 000 | OBSERVED |
| Appartement | 40 000 | 400 000 | TO_CONFIRM |
| Maison | 80 000 | 800 000 | TO_CONFIRM |
| Duplex | 150 000 | 1 500 000 | TO_CONFIRM |
| Local commercial | 50 000 | 1 000 000 | TO_CONFIRM |

Les quartiers marqués `premium` (Bonanjo, Bonapriso, Bonamoussadi à Douala ;
Bastos, Nlongkak à Yaoundé) **élargissent** la fourchette (facteur ×2) au lieu de
la remplacer : la source donne une tendance, pas un coefficient exact.

## Conditions d'entrée : la moitié du prix

Dans ce marché le loyer mensuel n'est pas le coût réel d'entrée. Le formulaire
collecte donc explicitement :

- **Avance** — mois de loyer payés d'avance. Habitude observée : 3 à 6 mois
  (`ADVANCE_MONTHS_TYPICAL`). Variable selon bailleur et standing.
- **Caution** — dépôt de garantie remboursable. Habitude observée : 1 à 2 mois
  (`CAUTION_MONTHS_TYPICAL`). Correspond au champ `deposit` du domaine.
- **Charges** — incluses ou en supplément (`ChargingPolicy`).
- **Frais de bail / frais d'agence** — commission d'agence ou de contrat ;
  capturée dans le texte libre des conditions.
- **Durée minimale** et **disponibilité** (date ou immédiat).

Cadre légal : loi n°2014/023 (baux à usage d'habitation). La loi n°2009/010
concerne la location-accession et n'encadre pas directement ces pratiques ; elle
est listée pour mémoire.

### Ce qui varie / à confirmer

- Le nombre exact de mois d'avance est **négociable** et varie par quartier.
- Les frais d'agence (souvent ~1 mois) varient selon l'agence.
- La fourchette par type est `TO_CONFIRM` : à affiner avec des données de
  transactions réelles.

## Glossaire (`EXPRESSIONS`)

| Expression | Sens | Confiance |
| --- | --- | --- |
| « mil » | milliers : `50 mil` = 50 000 FCFA (jamais millions) | OBSERVED |
| « avance » | mois de loyer payés à l'entrée | OBSERVED |
| « caution » | dépôt de garantie remboursable (1–2 mois) | OBSERVED |
| « frais de bail / d'agence » | frais de contrat / commission | OBSERVED |
| « chambre salon » | une chambre + un salon | OBSERVED |
| « sdb » | salle de bain | OBSERVED |
| « rdc » | rez-de-chaussée | OBSERVED |
| « entrée / conditions d'entrée » | coût de déplacement : avance + caution + frais | OBSERVED |
| « carré / en face de / derrière » | repère d'adresse familier | TO_CONFIRM |

## Règle d'usage

- Une valeur **connue** (quartier, ville) est résolue par l'index, en code.
- Un **montant en argot** (« mil ») est tranché par une règle, pas par le modèle.
- Une **fourchette** n'accepte ni ne refuse un prix : le prix de l'utilisateur
  reste maître tant qu'il passe les bornes de plausibilité du domaine.
