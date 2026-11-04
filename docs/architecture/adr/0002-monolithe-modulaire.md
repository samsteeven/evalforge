# ADR-002 : monolithe modulaire et paquets partagés

**État** : acceptée.

## Contexte

La difficulté d'EvalForge est l'orchestration des évaluations (concurrence, délais, reprises, reproductibilité), pas un domaine qui se découperait naturellement en services autonomes.

## Décision

- Une application FastAPI découpée par domaine (`apps/api/src/evalforge_api/modules/<domaine>`), chaque module avec ses modèles et ses routes. Un module en utilise un autre par ses fonctions publiques (`get_project`, `get_dataset_version`…), jamais en lisant ses tables à sa place.
- Deux paquets sans dépendance à FastAPI ni à la base : `evaluation_engine` et `llm_gateway`. L'API, les futurs workers et la future CLI partagent ainsi la même implémentation d'une évaluation.
- Un espace de travail uv : chaque application et chaque paquet a son `pyproject.toml`, un seul `uv.lock`.

## Conséquences

- Transactions simples (une base), déploiement simple.
- Les traitements longs sortiront dans des workers (ADR-003) sans changer le moteur.
- Discipline nécessaire : rien n'empêche techniquement un module d'importer les modèles d'un autre.
