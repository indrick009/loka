# Mettre Fapshi en service

Tout le code est en place. Il reste trois choses à faire côté Fapshi et une
dans `.env`. Ce document ne décrit que ça, plus les trois propriétés du
fournisseur qui expliquent pourquoi l'intégration est bâtie comme elle est.

## 1. Côté dashboard Fapshi

Créer un **service** (un par environnement). Fapshi émet les identifiants par
service, et la clé live n'est affichée **qu'une fois**.

Pour chaque service, renseigner :

| Champ | Valeur |
| --- | --- |
| Webhook URL | `https://<votre-domaine>/webhooks/payments/fapshi` |
| Webhook secret | la même chaîne que `PAYMENT__FAPSHI__WEBHOOK_SECRET` |

L'URL **doit être en https**. Fapshi renvoie le secret en clair dans l'en-tête
`x-wh-secret` : en http, il circule lisible et quiconque l'intercepte peut
poster n'importe quel paiement.

Le dashboard n'affiche pas si un secret est déjà positionné. Si vous n'en avez
pas la trace, remplacez-le des deux côtés plutôt que de le deviner.

**Whitelist IP** : facultative. Si vous l'activez, seules les IP listées
peuvent créer des transactions (`initiate-pay`, `direct-pay`, `payout`) ; une
requête venue d'ailleurs reçoit un 403 malgré des identifiants valides. Avec
une IP de sortie instable, ne l'activez pas.

## 2. Côté `.env`

```
PAYMENT__GATEWAY=fapshi
PAYMENT__FAPSHI__BASE_URL=https://sandbox.fapshi.com   # live : https://live.fapshi.com
PAYMENT__FAPSHI__API_USER=<apiuser du service>
PAYMENT__FAPSHI__API_KEY=<apikey du service>
PAYMENT__FAPSHI__WEBHOOK_SECRET=<le même secret que sur le dashboard>
```

Les deux environnements ont des hôtes **et** des identifiants distincts. Une
clé live pointée sur l'hôte sandbox reçoit un 403 — échec bruyant, voulu : la
confusion inverse encaisserait de la fausse monnaie en silence.

`PAYMENT__GATEWAY` vaut `mock` par défaut et doit le rester en local et dans la
suite de tests. Un nom inconnu lève une erreur au démarrage au lieu de retomber
sur le mock, parce qu'une faute de frappe en production accorderait tous les
déblocages gratuitement.

## 3. Le rattrapage, sur une planification

```
*/5 * * * *  python -m loka.interfaces.cli.reconcile_payments
```

Ce n'est pas optionnel. Voir ci-dessous.

## Pourquoi c'est bâti comme ça

**Le webhook ne porte pas de signature.** Fapshi renvoie le secret configuré
dans `x-wh-secret`. Cela prouve qui appelle ; cela ne prouve rien sur le
contenu du corps, et quiconque apprend le secret peut poster
`status: SUCCESSFUL` pour n'importe quelle transaction. Chaque notification est
donc reconfirmée par `GET /payment-status/{transId}`, et c'est cette réponse
qui règle le paiement. Le webhook est ramené à ce qu'il est : un signal qui dit
d'aller voir.

**Fapshi n'envoie qu'un webhook par événement et ne réessaie jamais.** Une
requête perdue pendant un déploiement, c'est un locataire débité et un contact
jamais révélé, sans aucune trace. D'où `reconcile_payments` : il reprend les
paiements restés en attente, demande au fournisseur ce qui s'est réellement
passé, et applique le verdict. Sans cette tâche planifiée, l'intégration a un
trou silencieux.

**Un lien de paiement vit 24 heures, pas 30 minutes.** La fenêtre vient
désormais du fournisseur (`link_ttl_seconds`), pas de la supposition de la
plateforme : annoncer une expiration à 30 minutes refuse un payeur revenu à la
deuxième heure sur une page que Fapshi aurait honorée.

## Ce qui n'est pas encore là

**Aucun flux WhatsApp n'initie de paiement.** `InitiateServiceFeeUseCase` n'est
appelé que par `POST /payments/listings/{id}/initiate`, une route
authentifiée. Le lien est créé, stocké et renvoyé — mais rien ne le met encore
dans la conversation du locataire. C'est l'étape suivante, et c'est du flux
conversationnel, pas de l'intégration.

**L'abonnement hebdomadaire des restaurants** (2 000 FCFA/semaine) n'est pas
implémenté. Il n'a volontairement pas été plié dans `ServiceFeePayment` : un
déblocage ponctuel et un abonnement récurrent ont des cycles de vie différents,
et les confondre revient à faire porter à un agrégat deux invariants qui se
contredisent.
