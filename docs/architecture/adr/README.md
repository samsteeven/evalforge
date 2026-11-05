# Décisions d'architecture

| ADR | Décision | État |
|---|---|---|
| [001](0001-python-fastapi.md) | Python et FastAPI pour tout le backend | acceptée, appliquée |
| [002](0002-monolithe-modulaire.md) | Monolithe modulaire et paquets partagés | acceptée, appliquée |
| 003 | Workers indépendants pour les traitements longs | acceptée, appliquée ([022](0022-execution-asynchrone.md)) |
| 004 | RabbitMQ (aio-pika) pour la communication asynchrone | acceptée, appliquée ([022](0022-execution-asynchrone.md)) |
| 005 | Outbox transactionnelle | acceptée, appliquée ([022](0022-execution-asynchrone.md)) |
| 006 | PostgreSQL pour la persistance | acceptée, appliquée |
| 007 | pgvector seulement si une fonctionnalité interne le justifie : EvalForge évalue des RAG externes | acceptée, hors MVP |
| 008 | Ragas et DeepEval derrière des adaptateurs internes | acceptée, appliquée ([021](0021-adaptateurs-ragas-deepeval.md)) |
| 009 | OpenTelemetry | acceptée, incrément 6 |
| [010](0010-react-typescript.md) | React et TypeScript pour l'interface (au lieu de Vue 3) | acceptée, appliquée |
| 011 | ECS Fargate plutôt que Kubernetes | acceptée, incrément 6 |
| 012 | S3 pour les artefacts volumineux | acceptée, incrément 3 |
| [013](0013-execution-dans-l-api-provisoire.md) | Exécution des runs dans le processus de l'API, en attendant les workers | remplacée par 022 |
| [014](0014-gabarits-restreints.md) | Gabarits de prompt limités à `{{ variable }}` | acceptée, appliquée |
| [015](0015-espace-par-en-tete-provisoire.md) | Espace de travail par en-tête, en attendant l'authentification | remplacée par 016 |
| [016](0016-sessions.md) | Jeton d'accès court, jeton de renouvellement à usage unique en cookie HttpOnly, rôles relus à chaque requête | acceptée, appliquée |
| [017](0017-protection-ssrf.md) | Protection SSRF au moment de la connexion | acceptée, appliquée |
| [018](0018-outils-de-test-du-front.md) | Vitest et Cypress pour le front | acceptée, appliquée |
| [019](0019-juge-llm.md) | Juge LLM natif à grilles versionnées ; réponse illisible = métrique indisponible | acceptée, appliquée |
| [020](0020-cli-porte-de-qualite.md) | CLI qui partage le moteur et sert de porte de qualité en CI | acceptée, appliquée |
| [021](0021-adaptateurs-ragas-deepeval.md) | Ragas et DeepEval derrière la passerelle EvalForge, en dépendances optionnelles | acceptée, appliquée |
| [022](0022-execution-asynchrone.md) | Exécution asynchrone : outbox, RabbitMQ, workers à bail, reprise sans doublon, file morte | acceptée, appliquée |
| [023](0023-detection-de-regression.md) | Baseline de projet et détection de régression appariée (McNemar exact) | acceptée, appliquée |
| [024](0024-evaluation-d-agents.md) | Agents évalués sur leur trajectoire, à travers un contrat HTTP ; agent de support de démonstration | acceptée, appliquée |
| [025](0025-webhooks.md) | Webhooks signés (HMAC), envoyés par les workers depuis les événements de fin de run | acceptée, appliquée |

Les ADR 003 à 012 sont rédigées en détail à l'incrément qui les met en œuvre ; leur justification d'origine est dans [vision.md](../vision.md).
