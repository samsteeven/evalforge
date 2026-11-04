# EvalForge : évaluer la qualité, la latence et le coût d'applications IA

Plateforme d'évaluation pour applications LLM, RAG et agents, en **Python (FastAPI)**. Une équipe y versionne ses jeux de tests et ses prompts, lance une expérience sur un ou plusieurs modèles, et obtient pour chaque réponse des métriques de qualité, la latence, les tokens et le coût. L'objectif à terme : comparer à une baseline et bloquer un déploiement qui dégrade la qualité.

> **État : incrément 1, la tranche verticale du backend.** Un projet, cinq questions, deux modèles, des résultats comparés. Pas encore d'interface, d'authentification ni de RabbitMQ : voir [la feuille de route](#feuille-de-route).

## Le parcours qui fonctionne

```
POST /api/v1/workspaces                         espace de travail (multi-organisation dès le départ)
POST /api/v1/projects                           « Customer Support AI », type rag
POST /api/v1/projects/{id}/datasets             jeu de tests
POST /api/v1/datasets/{id}/versions             version immuable, empreinte SHA-256 ; contenu identique → même version
POST /api/v1/projects/{id}/prompts              gabarit versionné : « Contexte : {{ context }} Question : {{ question }} »
POST /api/v1/projects/{id}/providers            modèle compatible OpenAI ou Ollama ; clé chiffrée, prix avec sa source
POST /api/v1/projects/{id}/experiments          prompt + 1 à 5 modèles + métriques
POST /api/v1/experiments/{id}/runs              202 Accepted ; exécution en arrière-plan, concurrence et délai réglables
GET  /api/v1/runs/{id}                          état, progression, configuration exacte exécutée (sans secrets)
GET  /api/v1/runs/{id}/results                  réponse, statut, latence, tokens, coût et métriques, par cas et par modèle
GET  /api/v1/runs/{id}/metrics                  agrégats par modèle : qualité, P50/P95, taux de réussite, tokens, coût
```

La documentation interactive est servie sur `/docs` (OpenAPI).

## Ce qui est vérifié, et ce qui ne l'est pas

| Élément | Vérifié | Pas vérifié |
|---|---|---|
| Parcours complet | Contre un vrai PostgreSQL migré par Alembic : 5 questions, 2 modèles (un faux fournisseur compatible OpenAI qui répond juste, un qui répond à côté), 10 résultats, comparaison des taux de réussite | **Aucun appel à un vrai modèle** : pas de clé utilisée, Ollama pas installé sur le poste de développement |
| Reproductibilité | Le run fige le prompt, la version du jeu (empreinte), les modèles, paramètres et prix ; une version de jeu ou de prompt modifiée en crée une nouvelle | |
| Pas de doublon | Un résultat rejoué ou un run relancé ne crée rien de plus et n'appelle aucun fournisseur. Les trois garde-fous (état du run, unicité du résultat, filtre d'espace) ont été cassés volontairement : chaque casse fait échouer un test | Deux processus qui traitent le même run en même temps (un seul processus pour l'instant) |
| Robustesse | Un cas en erreur (gabarit, fournisseur, délai) ne fait pas échouer le run ; une métrique indisponible n'est jamais comptée comme 0 ; un coût inconnu n'est jamais présenté comme un total | |
| Fournisseurs | 429 rejoué après le délai annoncé (plafonné), erreurs serveur rejouées avec délai croissant, refus d'authentification non rejoué, réponse illisible normalisée | Le comportement réel de DeepSeek, OpenAI ou Ollama |
| Isolation | Un autre espace de travail reçoit 404 sur projets, jeux, versions, cas, configurations, expériences, runs, résultats et agrégats | Il n'y a pas encore d'utilisateurs : l'espace vient d'un en-tête ([ADR-015](docs/architecture/adr/0015-espace-par-en-tete-provisoire.md)) |
| Secrets | Clé d'API chiffrée en base (Fernet), jamais renvoyée, absente de la configuration figée et des messages d'erreur | Rotation de la clé de chiffrement |
| Arrêt de l'API | Un run en cours est marqué « interrupted » au redémarrage au lieu de rester « running » | Reprise automatique (prévue avec RabbitMQ) |
| Migrations | La CI vérifie qu'aucune différence ne reste entre les modèles et les migrations (`alembic check`) | |

38 tests : 25 sur le moteur et la passerelle (sans base), 13 sur l'API contre PostgreSQL.

## Architecture

Monolithe modulaire FastAPI, découpé par domaine (`workspaces`, `projects`, `datasets`, `providers`, `prompts`, `experiments`, `evaluations`), et deux paquets réutilisables qui ne connaissent ni FastAPI ni la base :

```
apps/api                    FastAPI, SQLAlchemy 2 async, Alembic : l'API et l'exécution des runs
packages/evaluation_engine  contrats, gabarits, évaluateurs, orchestration (concurrence, délais), agrégats
packages/llm_gateway        appels normalisés aux fournisseurs : délais, reprises, tokens, erreurs, capacités
```

Le moteur sert aussi la future CLI et les futurs workers : une évaluation n'a qu'une implémentation. Une réponse produite (`evaluation_results`) est séparée de ses mesures (`metric_results`) : ajouter une métrique ne change pas le schéma.

La cible complète (workers RabbitMQ avec outbox, Ragas et DeepEval, agents, sécurité, AWS) est décrite dans [docs/architecture/vision.md](docs/architecture/vision.md), et les décisions dans [les ADR](docs/architecture/adr/README.md).

## Démarrer

Prérequis : [uv](https://docs.astral.sh/uv/), Docker.

```bash
docker compose up -d postgres                     # PostgreSQL 18 sur le port 5435
cp .env.example .env                              # puis renseigner EVALFORGE_SECRET_KEY (commande dans le fichier)
uv sync
uv run alembic -c apps/api/alembic.ini upgrade head
uv run uvicorn evalforge_api.main:create_app --factory --reload
```

Un modèle local avec Ollama : `provider_kind: "ollama"`, `base_url: "http://localhost:11434/v1"`, sans clé.

## Tests

```bash
uv run pytest            # PostgreSQL jetable via Testcontainers (données en mémoire)
uv run ruff check . && uv run ruff format --check .
```

`EVALFORGE_TEST_DATABASE_URL` fait tourner les tests sur une base déjà démarrée au lieu d'un conteneur.

## Limites actuelles

- **Exécution dans le processus de l'API** ([ADR-013](docs/architecture/adr/0013-execution-dans-l-api-provisoire.md)) : un run en cours est perdu si l'API s'arrête. Remplacée par outbox, RabbitMQ et workers à l'incrément 4.
- **Pas d'authentification** : l'espace de travail vient de l'en-tête `X-Workspace-Id`. Ne pas exposer cette version.
- **URL de fournisseur libre** : l'API appelle l'adresse saisie, y compris une adresse interne. La protection SSRF (liste d'adresses autorisées, contrôle à la connexion) vient avec l'authentification.
- **Métriques déterministes seulement** (`exact_match`, `contains_expected`) : pas encore de juge LLM ni de métriques RAG (Ragas, DeepEval).
- **Import JSON seulement**, 1 000 cas au plus par version ; pas de CSV.
- **Résultats paginés en mémoire** : suffisant pour quelques milliers de résultats par run, pas au-delà.

## Feuille de route

| Incrément | Contenu | État |
|---|---|---|
| 1 | Tranche verticale backend : projets, jeux versionnés, prompts versionnés, fournisseurs, expériences, runs, métriques déterministes, latence, tokens, coût | **fait** |
| 2 | Interface Vue 3 (projets, jeux, expériences, détail d'un run), authentification JWT et rôles, protection SSRF | à faire |
| 3 | Juge LLM, adaptateurs Ragas et DeepEval, import CSV, CLI, rapport HTML | à faire |
| 4 | Outbox, RabbitMQ (aio-pika), workers, reprises et file morte ; baseline et détection de régression | à faire |
| 5 | Traces et trajectoires d'agents, évaluation des appels d'outils, webhooks et contrôle qualité en CI | à faire |
| 6 | Tests de prompt injection, OpenTelemetry, charge, Terraform et AWS (ECS Fargate) | à faire |

## Structure

```
apps/api/
  src/evalforge_api/core/       configuration, base, chiffrement des secrets, espace de travail courant
  src/evalforge_api/modules/    un dossier par domaine : modèles et routes
  migrations/                   Alembic
  tests/                        tests d'API contre PostgreSQL, faux fournisseur compatible OpenAI
packages/evaluation_engine/     moteur d'évaluation et ses tests
packages/llm_gateway/           passerelle vers les fournisseurs et ses tests
docs/architecture/              vision complète et ADR
```
