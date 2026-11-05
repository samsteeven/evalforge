# ADR-013 : exécution des runs dans le processus de l'API (provisoire)

**État** : remplacée par l'[ADR-022](0022-execution-asynchrone.md) à l'incrément 4.

## Contexte

La tranche verticale doit montrer un run de bout en bout. Construire tout de suite l'outbox, RabbitMQ et les workers retarderait le premier parcours utilisable de plusieurs semaines.

## Décision

`POST /experiments/{id}/runs` enregistre le run puis répond 202 ; une tâche asyncio de l'API l'exécute. Ce qui préparera la suite est déjà en place :

- le run est dans un état explicite (`queued`, `running`, `completed`, `failed`, `interrupted`) et n'est exécuté que s'il est `queued` ;
- chaque résultat est enregistré dès qu'il est prêt, unique par (run, cas, modèle) ;
- la configuration exécutée est figée dans le run.

## Conséquences

- Un arrêt de l'API perd le run en cours. Au démarrage suivant, les runs restés `queued` ou `running` passent `interrupted`, avec un message : on ne laisse pas croire qu'ils tournent encore.
- Ce marquage suppose **une seule instance** de l'API : avec deux, l'une interromprait les runs de l'autre.
- Les longs runs consomment les ressources de l'API.
