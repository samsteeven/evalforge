# Vision et architecture cible

Ce document résume le blueprint technique du projet : ce qu'EvalForge doit devenir, et dans quel ordre. Ce qui existe déjà est indiqué dans le [README](../../README.md).

## Le problème

Une équipe peut intégrer un modèle dans son application sans savoir si ses réponses sont exactes, s'il invente, si son RAG retrouve les bons documents, si son agent utilise correctement ses outils, si un changement de prompt dégrade le résultat, s'il est trop lent ou trop coûteux, ou s'il résiste à une injection de prompt. EvalForge rend ces éléments mesurables.

## Sept modules fonctionnels

| Module | Ce qu'il mesure |
|---|---|
| Qualité | Exactitude, pertinence, cohérence, fidélité aux sources, hallucinations ; juge LLM et revue humaine |
| Benchmark de modèles | Plusieurs modèles sur les mêmes cas, prompts et paramètres : qualité, latence, tokens, coût |
| RAG | Séparément la récupération (context precision, context recall) et la génération (faithfulness, answer relevancy) |
| Agents | Trajectoire : tâche accomplie, bon outil, bons arguments, plan respecté, étapes inutiles |
| Sécurité | Injection de prompt, fuite de données, contournement de garde-fous, permissions |
| Régression | Comparaison à une baseline avant déploiement ; versions de prompts et de jeux ; seuils en CI |
| Automatisation | Déclencheurs, webhooks, planification ; l'intégration (Slack, e-mail, GitHub) est laissée à n8n |

Le socle commun, construit en premier : **jeu de données → expérience → run → résultats → rapport**.

## Architecture

Monolithe modulaire (FastAPI) et workers indépendants pour les traitements longs, plutôt que des microservices : la complexité est dans l'orchestration des évaluations, pas dans le découpage du domaine.

```
React + TypeScript ──► FastAPI (/api/v1, SSE pour la progression)
                          │  PostgreSQL : métadonnées, résultats, outbox
                          │  Redis : cache, verrous, limites
                          ▼
                 Outbox ─► RabbitMQ ─► workers Python (évaluation, rapports)
                                          │
                                          ▼
                          Passerelle LLM ─► API compatibles OpenAI, DeepSeek, Ollama…
OpenTelemetry (traces, métriques, journaux) ; S3/MinIO pour les rapports et artefacts volumineux.
```

EvalForge est extérieur au système évalué : il appelle un modèle directement, ou une application RAG ou un agent à travers un connecteur. Il ne suppose pas que tous les RAG utilisent la même technologie.

### Modules backend

`identity`, `workspaces`, `projects`, `datasets`, `providers`, `prompts`, `experiments`, `evaluations`, `metrics`, `tracing`, `benchmarks`, `automation`, `reports`, `security`. Chaque module possède ses modèles, schémas et routes ; ils communiquent par fonctions publiques ou par événements, sans imports circulaires.

### Paquets partagés

- `evaluation_engine` : contrats, évaluateurs (déterministes, juge LLM, RAG, agents, sécurité), adaptateurs Ragas et DeepEval, orchestration, agrégats. Utilisé par l'API, les workers et la CLI.
- `llm_gateway` : normalisation des appels, délais, reprises, tokens, coûts, erreurs, capacités déclarées par adaptateur, secrets masqués.
- `event_contracts` (à venir) : événements versionnés.

## Modèle de données

`Workspace`, `WorkspaceMember`, `User` ; `AIProject` ; `Dataset`, `DatasetVersion`, `TestCase` ; `ModelConfiguration` ; `PromptVersion` ; `Experiment` ; `EvaluationRun`, `EvaluationResult`, `MetricResult` ; `Trace`, `TraceSpan` ; `EvaluationBaseline` ; `AutomationTrigger` ; `OutboxEvent`, `ProcessedMessage`.

Décisions structurantes :

- **Versionner jeux et prompts** : un run référence une version immuable, jamais un jeu modifiable.
- **Figer la configuration exacte** du modèle (fournisseur, nom, paramètres, prix) et conserver l'identifiant de version renvoyé par le fournisseur.
- **Séparer résultats et métriques** : ajouter une métrique ne change pas le schéma.
- **Artefacts volumineux hors PostgreSQL** (S3), PostgreSQL garde les références.
- **Multi-organisation dès le départ** : tout est rattaché à un espace de travail, filtré à chaque requête.

Un cas de test suit un contrat commun (`input`, `expected`, `context`, `metadata`), et plus tard `tools`, `trajectory`, `constraints` pour les agents, validé par Pydantic selon son type.

## Moteur d'évaluation

Contrat commun `Evaluator.evaluate(EvaluationInput) -> MetricOutput` ; chaque adaptateur convertit vers ce contrat, pour ne dépendre d'aucune structure de bibliothèque tierce.

| Famille | Métriques |
|---|---|
| Qualité | Correctness, Relevancy, Coherence |
| Hallucination | Faithfulness, Hallucination Rate |
| RAG | Context Precision, Context Recall, Answer Relevancy |
| Agents | Task Completion, Tool Correctness, Step Efficiency |
| Performance | P50, P95, P99, débit |
| Coût | Tokens d'entrée et de sortie, coût par requête |
| Fiabilité | Taux de réussite, d'erreur, de dépassement de délai |
| Sécurité | Injections réussies, fuites, violations de politique |

Quatre méthodes : **déterministe** (en premier : correspondance, schéma JSON, expressions, calculs, arguments d'outils), **juge LLM** (avec sa méthode, sa version et ses biais connus : ce n'est pas une vérité absolue), **humaine**, **statistique** (distributions, écart à la baseline, intervalles de confiance).

Diagnostic RAG : context recall faible → récupération ; recall élevé mais faithfulness faible → génération ; faithfulness élevée mais relevancy faible → réponse fidèle au contexte mais à côté de la question.

## Exécution asynchrone

`POST /experiments/{id}/runs` enregistre le run et un événement d'outbox dans la même transaction, répond 202 ; un publieur envoie `evaluation.requested` avec confirmation du courtier ; un worker vérifie l'idempotence, exécute les cas, enregistre au fil de l'eau et acquitte après persistance.

Événements : `evaluation.requested`, `.started`, `.progressed`, `.completed`, `.failed`, `report.requested`, `benchmark.requested`. Enveloppe : `event_id`, `event_type`, `schema_version`, `occurred_at`, `correlation_id`, `payload`.

Garanties : outbox transactionnelle, idempotence (RabbitMQ livre au moins une fois), confirmations de publication, reprises avec délai croissant, file morte, acquittement après persistance, heartbeat, délais sur tous les appels externes. Deux workers au départ : évaluation et rapports.

## Sécurité

JWT avec rotation des jetons de renouvellement ; rôles Owner, Admin, Member, Viewer ; isolation des espaces ; journal des actions sensibles. Clés d'API chiffrées au repos (Secrets Manager et KMS en production), masquées dans les journaux. Expurgation configurable des traces. Import de fichiers validé (format, taille). Appels sortants vers une liste d'adresses autorisées, protection SSRF. Exécutions risquées d'agents isolées dans des conteneurs limités (CPU, mémoire, réseau, durée), jamais dans le processus de l'API.

## Observabilité

OpenTelemetry vers Prometheus, Tempo, Loki et Grafana. Mesurer EvalForge lui-même : API (débit, P95, erreurs), workers (file, durée, échecs), RabbitMQ (non acquittés, file morte), PostgreSQL, Redis, fournisseurs (latence, limites, délais), évaluations (cas par minute, durée des runs).

## Déploiement

Local : Docker Compose (API, workers, PostgreSQL, RabbitMQ, Redis, MinIO, pile d'observabilité). AWS : CloudFront et S3 pour le front, ALB, ECS Fargate (API et workers), RDS PostgreSQL, Amazon MQ, ElastiCache, S3, Secrets Manager, KMS, CloudWatch ; Terraform par modules et environnements. ECS plutôt que Kubernetes : moins d'exploitation pour ce périmètre.

Ajout au blueprint (2026-11-05) : les mêmes images se déploient aussi sur un cloud privé. Des manifestes Kubernetes compatibles OpenShift (conteneurs non root, utilisateur arbitraire, aucun volume sensible), vérifiés en CI sur un cluster local, et un playbook Ansible qui installe la plateforme sur une machine virtuelle. Terraform crée l'infrastructure, Ansible configure les machines.

## Agent de démonstration

Ajout au blueprint (2026-11-05) : EvalForge évalue des agents, il en contient aussi un, construit pour l'occasion. Un agent de support client avec quelques outils (recherche de commande, politique de remboursement, création de ticket), appelés par le modèle, et des traces de chaque étape. Il sert de cible aux évaluations de trajectoire (bon outil, bons arguments, étapes inutiles) et montre la construction d'un agent, pas seulement sa mesure.

## Politique qualité (exemple)

```yaml
evaluation_policy:
  name: production-readiness
  quality: { faithfulness: { minimum: 0.95 }, answer_relevancy: { minimum: 0.90 } }
  performance: { p95_latency_ms: { maximum: 3000 } }
  reliability: { success_rate: { minimum: 0.99 } }
  regression: { max_quality_drop: 0.02 }
  security: { critical_vulnerabilities: { maximum: 0 } }
```

Seuils configurables par projet ; une métrique indisponible se distingue d'une métrique en échec.

## Scénarios de test obligatoires

| Scénario | État |
|---|---|
| Une évaluation n'est pas perdue si l'API redémarre après avoir enregistré son événement d'outbox | vérifié : l'événement attend dans l'outbox et part au retour du courtier |
| Un événement consommé deux fois ne crée pas deux résultats | vérifié : message rejoué contre un vrai RabbitMQ, rien n'est refait |
| Un 429 du fournisseur déclenche une reprise contrôlée | vérifié |
| Un worker interrompu permet de reprendre ou de marquer le run interrompu | vérifié : un autre worker reprend les seules réponses manquantes |
| Un espace de travail n'accède jamais aux données d'un autre | vérifié |
| Un changement de prompt se compare à une baseline | vérifié : comparaison appariée, cas cassés, McNemar, dans l'API, l'interface et la CLI |
| Une métrique indisponible n'est pas transformée en zéro | vérifié |

## Démonstration finale visée

Une application RAG sur DeepSeek : projet « Customer Support AI », 100 questions avec réponses attendues, DeepSeek et Ollama configurés, faithfulness, answer relevancy et context precision ; l'expérience compare les deux modèles en parallèle, enregistre résultats, tokens, coûts et durées ; on modifie le prompt, on relance, EvalForge signale une régression par rapport à la baseline.
