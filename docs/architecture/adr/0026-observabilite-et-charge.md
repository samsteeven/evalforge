# ADR-026 : traces OpenTelemetry de bout en bout, et test de charge qui s'en sert

**État** : acceptée.

## Contexte

Un run traverse l'API, une transaction (outbox), RabbitMQ, un worker, des modèles et parfois un agent dans un autre processus. Quand il est lent ou échoue, il faut savoir où, sans recouper les journaux de quatre services à la main. Il fallait aussi savoir comment l'API tient la charge, et pourquoi elle ne la tient pas.

## Décision

- **OpenTelemetry**, l'API seule dans les bibliothèques (`llm_gateway`, `evaluation_engine` : sans SDK, chaque span est un no-op), le SDK dans les services. Export OTLP/HTTP si `OTEL_EXPORTER_OTLP_ENDPOINT` est défini (Jaeger dans `docker compose --profile observability`), en console avec `EVALFORGE_OTEL_CONSOLE=1`, rien sinon.
- **Une trace par run, d'un bout à l'autre** : le contexte W3C de la requête est écrit dans l'enveloppe de l'événement (outbox), le worker le reprend (span `process evaluation.requested`, attributs `messaging.*`) ; l'appel HTTP vers un agent porte le `traceparent`, que l'agent reprend. La fin du run (événement écrit par le worker) prolonge la même trace jusqu'au webhook. Une reprise après panne ou une attente dans l'outbox ne coupe pas la trace.
- **Conventions GenAI** pour les appels aux modèles (`chat <modèle>`, `gen_ai.request.model`, `gen_ai.usage.*`, tentatives), l'agent (`invoke_agent`) et ses outils (`execute_tool`). **Ni prompt, ni réponse, ni clé** dans les attributs : une trace se partage plus largement qu'une base.
- Instrumentation FastAPI, httpx et SQLAlchemy. Cette dernière refuse SQLAlchemy 2.1 par déclaration de version (`< 2.1`) alors que l'API d'événements utilisée n'a pas changé : elle est activée avec `skip_dep_check`, et les spans SQL ont été vérifiés.
- **Test de charge Locust** (`loadtests/`, groupe uv `load`) : 20 utilisateurs qui créent leur espace puis lisent et lancent des runs exécutés par les workers. Seuils en CI : aucune erreur, P95 des lectures sous 500 ms, chaque run terminé.

## Ce que la charge a trouvé, et ce que les traces ont expliqué

1. **Argon2 dans la boucle d'événements** : chaque inscription gelait toute l'API (médiane d'inscription 5,8 s sous 20 utilisateurs). Le hachage passe dans un fil, au plus `cpu/2` à la fois : 64 Mio par calcul, une rafale de connexions ne doit pas épuiser la mémoire. Inscription : 0,22 s.
2. **`localhost` coûte 2 s par connexion sous Windows** : il essaie d'abord `::1`, où Docker n'écoute pas. Les traces montraient des spans `connect` de 2 050 ms pile. Adresses par défaut en `127.0.0.1`.
3. **Pool de connexions trop petit** : au-delà de 5 connexions, SQLAlchemy en ouvrait de nouvelles à chaque pointe, et une connexion neuve à travers la redirection de ports de Docker Desktop coûte jusqu'à une seconde sous charge. Pool réglable, 20 + 10 par défaut.

Résultat sur le poste de développement (tout sur une machine, Locust compris) : P95 des lectures de 32 à 48 ms au lieu de 1,6 s. Environ 2 % des requêtes dépassent encore la seconde ; leurs traces montrent l'ouverture d'une connexion. À confirmer sur le runner Linux de la CI, sans Docker Desktop.

## Conséquences

- Le traceparent voyage dans l'enveloppe, pas dans un en-tête AMQP : il survit à l'outbox et aux republications de la file de reprise.
- Le test de charge mesure une machine partagée avec tous les services : ses chiffres comparent deux versions, ils ne dimensionnent pas une production.
