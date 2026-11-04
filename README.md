# EvalForge : évaluer la qualité, la latence et le coût d'applications IA

Plateforme d'évaluation pour applications LLM, RAG et agents : **Python (FastAPI)** côté serveur, **React + TypeScript** côté interface. Une équipe y versionne ses jeux de tests et ses prompts, lance une expérience sur un ou plusieurs modèles, et obtient pour chaque réponse des métriques de qualité, la latence, les tokens et le coût. L'objectif à terme : comparer à une baseline et bloquer un déploiement qui dégrade la qualité.

> **État : incrément 2.** Comptes, rôles et sessions, protection SSRF, et une interface complète du projet jusqu'à la comparaison des modèles. Pas encore de juge LLM ni de RabbitMQ : voir [la feuille de route](#feuille-de-route).

![Run d'évaluation : deux modèles comparés sur cinq questions](docs/screenshots/run-comparaison.png)

*Capture prise par le test Cypress de bout en bout : le « modèle sourcé » répond à partir du contexte fourni, le « modèle vague » répond toujours « Je ne sais pas ». Le second n'a pas de prix renseigné : son coût est « inconnu », jamais 0.*

## Le parcours

Dans l'interface : inscription (un espace de travail est créé), projet « Customer Support AI », import d'une version de jeu de données en JSON, prompt versionné, configuration de deux modèles, expérience, lancement d'un run, puis comparaison qualité / performance / coût et détail de chaque réponse.

Côté API (`/docs` pour la documentation interactive) :

```
POST /api/v1/auth/register | login | refresh | logout     comptes et sessions
POST /api/v1/projects                                      projet, type rag
POST /api/v1/datasets/{id}/versions                        version immuable, empreinte SHA-256 ; contenu identique → même version
POST /api/v1/projects/{id}/prompts                         gabarit versionné : « Contexte : {{ context }} Question : {{ question }} »
POST /api/v1/projects/{id}/providers                       modèle compatible OpenAI ou Ollama ; clé chiffrée, prix avec sa source
POST /api/v1/projects/{id}/experiments                     prompt + 1 à 5 modèles + métriques
POST /api/v1/experiments/{id}/runs                         202 Accepted ; exécution en arrière-plan
GET  /api/v1/runs/{id} | results | metrics                 progression, réponses, agrégats par modèle
```

Toutes les routes métier demandent `Authorization: Bearer …` et l'en-tête `X-Workspace-Id` de l'espace courant.

## Ce qui est vérifié, et ce qui ne l'est pas

| Élément | Vérifié | Pas vérifié |
|---|---|---|
| Parcours complet | **Cypress dans un vrai navigateur**, contre l'API, PostgreSQL et un faux fournisseur HTTP : de l'inscription à la comparaison (10 réponses, 100 % contre 0 %, coût connu contre « inconnu »). En CI à chaque push | **Aucun appel à un vrai modèle** : pas de clé utilisée, Ollama pas installé sur le poste de développement |
| Sessions | Jeton d'accès de 15 min ; jeton de renouvellement en cookie `HttpOnly`, remplacé à chaque usage ; un jeton rejoué révoque toute la session ; en-tête anti-CSRF exigé ; jetons expirés ou falsifiés refusés | Plusieurs onglets qui renouvellent exactement au même instant ([ADR-016](docs/architecture/adr/0016-sessions.md)) |
| Rôles | Lecteur en lecture seule, membre qui évalue, administrateur seul à configurer un modèle et sa clé ; un membre retiré perd l'accès à la requête suivante ; le dernier propriétaire ne peut pas être retiré | Journal des actions sensibles |
| Isolation | Un autre espace reçoit 404 sur projets, jeux, versions, cas, configurations, expériences, runs, résultats et agrégats | Audit de sécurité externe |
| Protection SSRF | Adresses internes refusées à l'enregistrement ; à chaque appel, l'adresse résolue est vérifiée puis contactée directement (le DNS ne peut pas changer entre les deux) ; 13 familles d'adresses testées, dont métadonnées cloud et IPv4 déguisée en IPv6 | Un vrai DNS piégé (simulé par un résolveur de test) |
| Garde-fous | Six protections cassées volontairement (état du run, unicité d'un résultat, filtre d'espace, révocation de la famille de jetons, contrôle des rôles, blocage des adresses internes) : chacune fait échouer un test | |
| Reproductibilité | Le run fige le prompt, la version du jeu (empreinte), les modèles, paramètres et prix | |
| Robustesse | Un cas en erreur ne fait pas échouer le run ; une métrique indisponible n'est jamais comptée comme 0 ; un coût inconnu n'est jamais présenté comme un total | |
| Fournisseurs | 429 rejoué après le délai annoncé (plafonné), erreurs serveur rejouées avec délai croissant, refus d'authentification et destination interdite non rejoués | Le comportement réel de DeepSeek, OpenAI ou Ollama |
| Contrat front / API | Les types TypeScript sont générés depuis l'OpenAPI ; la CI échoue si l'un des deux n'est plus à jour | |
| Migrations | `alembic check` en CI : aucune différence entre modèles et migrations | |

85 tests automatisés : 78 en Python (30 passerelle, 17 moteur, 31 API contre PostgreSQL), 5 tests unitaires du front (Vitest), 2 parcours Cypress.

## Architecture

```
frontend/                    React 19, TypeScript, Vite, TanStack Query, React Router, Tailwind ; client typé openapi-fetch
apps/api                     FastAPI, SQLAlchemy 2 async, Alembic : l'API et l'exécution des runs
packages/evaluation_engine   contrats, gabarits, évaluateurs, orchestration (concurrence, délais), agrégats
packages/llm_gateway         appels aux fournisseurs : délais, reprises, tokens, erreurs, capacités, protection SSRF
scripts/fake_llm.py          faux fournisseur compatible OpenAI pour la démonstration et le test de bout en bout
```

Monolithe modulaire découpé par domaine (`identity`, `workspaces`, `projects`, `datasets`, `providers`, `prompts`, `experiments`, `evaluations`). Le moteur ne connaît ni FastAPI ni la base : les futurs workers et la future CLI l'utiliseront tel quel. La cible complète est décrite dans [docs/architecture/vision.md](docs/architecture/vision.md), les décisions dans [les ADR](docs/architecture/adr/README.md).

## Démarrer

Prérequis : [uv](https://docs.astral.sh/uv/), Node.js 24, Docker.

```bash
docker compose up -d postgres                     # PostgreSQL 18 sur le port 5435
cp .env.example .env                              # renseigner les deux clés (commandes dans le fichier)
uv sync
uv run alembic -c apps/api/alembic.ini upgrade head
uv run uvicorn evalforge_api.main:create_app --factory --reload     # API sur le port 8000

cd frontend && npm ci && npm run dev              # interface sur http://localhost:5173
```

Sans clé d'API : `uv run python scripts/fake_llm.py` lance un faux fournisseur sur `http://127.0.0.1:8100/v1` (modèles `grounded` et `vague`), à déclarer comme modèle « Compatible OpenAI » avec `EVALFORGE_ALLOW_PRIVATE_PROVIDER_URLS=true`. Un modèle local avec Ollama : type « Ollama », `http://localhost:11434/v1`, même réglage.

## Tests

```bash
uv run pytest                                     # PostgreSQL jetable via Testcontainers
uv run ruff check . && uv run ruff format --check .
cd frontend
npm run typecheck && npm run lint && npm test     # types, lint, Vitest
npm run e2e                                       # Cypress : démarre faux fournisseur, API et front (base migrée requise)
```

`EVALFORGE_TEST_DATABASE_URL` fait tourner les tests Python sur une base déjà démarrée. `EVALFORGE_SCREENSHOTS=1 npm run e2e` reprend les captures du README (dans `frontend/cypress/screenshots`).

Après une modification de l'API : `uv run python -m evalforge_api.openapi_export frontend/openapi.json`, puis `npm run api:types` dans `frontend`.

## Limites actuelles

- **Exécution dans le processus de l'API** ([ADR-013](docs/architecture/adr/0013-execution-dans-l-api-provisoire.md)) : un run en cours est perdu si l'API s'arrête (il est marqué « interrupted »). Remplacée par outbox, RabbitMQ et workers à l'incrément 4.
- **Pas d'e-mail** : un administrateur ajoute un compte existant par son adresse ; pas d'invitation ni de réinitialisation de mot de passe. L'ajout révèle si une adresse a un compte, à un administrateur seulement.
- **Métriques déterministes seulement** (`exact_match`, `contains_expected`) : pas encore de juge LLM ni de métriques RAG.
- **Import JSON seulement**, 1 000 cas au plus par version.
- **Pas de limite de débit** sur la connexion.

## Feuille de route

| Incrément | Contenu | État |
|---|---|---|
| 1 | Tranche verticale backend : projets, jeux et prompts versionnés, fournisseurs, expériences, runs, métriques déterministes, latence, tokens, coût | **fait** |
| 2 | Interface React + TypeScript, comptes, sessions et rôles, protection SSRF, tests Cypress | **fait** |
| 3 | Juge LLM, adaptateurs Ragas et DeepEval, import CSV, CLI, rapport HTML | à faire |
| 4 | Outbox, RabbitMQ (aio-pika), workers, reprises et file morte ; baseline et détection de régression | à faire |
| 5 | Traces et trajectoires d'agents, évaluation des appels d'outils, webhooks et contrôle qualité en CI | à faire |
| 6 | Tests de prompt injection, OpenTelemetry, charge, Terraform et AWS (ECS Fargate) | à faire |

## Structure

```
apps/api/
  src/evalforge_api/core/       configuration, base, sécurité (mots de passe, jetons), secrets, espace courant et rôles
  src/evalforge_api/modules/    un dossier par domaine : modèles et routes
  migrations/                   Alembic
  tests/                        tests d'API contre PostgreSQL, faux fournisseur compatible OpenAI
packages/evaluation_engine/     moteur d'évaluation et ses tests
packages/llm_gateway/           passerelle vers les fournisseurs, protection SSRF, et leurs tests
frontend/
  src/api/                      client typé et types générés depuis openapi.json
  src/auth/                     session : jeton en mémoire, renouvellement, espace courant
  src/features/                 jeux de données, prompts, modèles, expériences, comparaison des runs
  cypress/                      parcours de bout en bout
docs/architecture/              vision complète et ADR
```
