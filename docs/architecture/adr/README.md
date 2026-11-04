# Décisions d'architecture

| ADR | Décision | État |
|---|---|---|
| [001](0001-python-fastapi.md) | Python et FastAPI pour tout le backend | acceptée, appliquée |
| [002](0002-monolithe-modulaire.md) | Monolithe modulaire et paquets partagés | acceptée, appliquée |
| 003 | Workers indépendants pour les traitements longs | acceptée, incrément 4 |
| 004 | RabbitMQ (aio-pika) pour la communication asynchrone | acceptée, incrément 4 |
| 005 | Outbox transactionnelle | acceptée, incrément 4 |
| 006 | PostgreSQL pour la persistance | acceptée, appliquée |
| 007 | pgvector seulement si une fonctionnalité interne le justifie : EvalForge évalue des RAG externes | acceptée, hors MVP |
| 008 | Ragas et DeepEval derrière des adaptateurs internes | acceptée, incrément 3 |
| 009 | OpenTelemetry | acceptée, incrément 6 |
| [010](0010-react-typescript.md) | React et TypeScript pour l'interface (au lieu de Vue 3) | acceptée, incrément 2 |
| 011 | ECS Fargate plutôt que Kubernetes | acceptée, incrément 6 |
| 012 | S3 pour les artefacts volumineux | acceptée, incrément 3 |
| [013](0013-execution-dans-l-api-provisoire.md) | Exécution des runs dans le processus de l'API, en attendant les workers | provisoire |
| [014](0014-gabarits-restreints.md) | Gabarits de prompt limités à `{{ variable }}` | acceptée, appliquée |
| [015](0015-espace-par-en-tete-provisoire.md) | Espace de travail par en-tête, en attendant l'authentification | provisoire |

Les ADR 003 à 012 sont rédigées en détail à l'incrément qui les met en œuvre ; leur justification d'origine est dans [vision.md](../vision.md).
