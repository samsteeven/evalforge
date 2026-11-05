# ADR-022 : exécution asynchrone par outbox, RabbitMQ et workers

**État** : acceptée. Remplace l'ADR-013 (exécution dans l'API) et met en œuvre les ADR 003, 004 et 005.

## Contexte

Un run peut durer des minutes. Exécuté dans le processus de l'API, il était perdu au moindre redémarrage et consommait les ressources de l'API. Il fallait aussi garantir qu'un run demandé est exécuté, une seule fois, même si l'API, le courtier ou un worker tombe au mauvais moment.

## Décision

```
POST /experiments/{id}/runs ── une transaction ──► evaluation_runs (queued) + outbox_events (evaluation.requested)
                                                          │ publieur (API et workers), SKIP LOCKED, confirmation du courtier
                                                          ▼
                       <préfixe>.events (topic) ──► <préfixe>.evaluations (quorum) ──► worker(s)
                                                          ▲            │ erreur : délai croissant
                                                          └── .retry ◄─┘ puis .dead (file morte) et run en échec
```

- **Outbox transactionnelle** : le run et son événement sont validés ensemble. Courtier injoignable : l'événement attend, avec son nombre de tentatives et la dernière erreur, et part au retour du courtier. Plusieurs publieurs se répartissent le travail (`FOR UPDATE SKIP LOCKED`).
- **Worker séparé** (`evalforge-worker`, aio-pika) : même code et même modèle de données que l'API, autre processus et autre conteneur. Les workers se multiplient sans toucher à l'API.
- **Bail et battements** : le worker prend le run (`queued → running`) dans une seule requête conditionnelle, puis renouvelle `heartbeat_at`. Un run dont le bail a expiré peut être repris par un autre worker.
- **Acquittement après enregistrement** : RabbitMQ relivre le message d'un worker arrêté. Le suivant reprend le run et ne produit que les réponses manquantes ; chaque réponse est unique par (run, cas, modèle).
- **Idempotence** : `processed_messages` (message, consommateur) écrit dans la même transaction que la fin du run et l'événement `evaluation.completed` / `evaluation.failed`.
- **Run tenu par un autre worker vivant** : le message part en file d'attente pour un bail, sans compter de tentative.
- **Erreurs** : nouvelle tentative avec délai croissant (expiration par message dans une file sans consommateur, qui renvoie ensuite vers la file principale), puis file morte avec la dernière erreur en en-tête, et run en échec. Un message illisible part directement en file morte.
- **Files quorum**, délai d'acquittement porté à 6 h pour les longs runs.

## Conséquences

- Les scénarios 1, 2 et 4 du blueprint sont vérifiés contre un vrai RabbitMQ (voir `apps/api/tests/test_async_execution.py`).
- L'expiration par message bloque en tête de file : un message à long délai retarde ceux qui le suivent dans la file d'attente. Acceptable avec trois tentatives ; des files par palier de délai le corrigeraient.
- Le statut `interrupted` n'est plus produit : un run abandonné est repris, pas marqué.
- Pour ce consommateur, `processed_messages` double l'état du run : un run terminé est reconnu de toute façon, et la casse volontaire de cette vérification ne fait échouer aucun test. La table reste, pour les consommateurs à venir (webhooks) qui n'auront pas d'état métier pour les protéger. Les quatre autres garde-fous (bail valide, reprise des seules réponses manquantes, événement dans la transaction, file morte) ont été cassés un par un : chacun fait échouer un test.
- Pas encore de progression poussée en direct (SSE) : le front interroge le run chaque seconde.
