# ADR-001 : Python et FastAPI pour tout le backend

**État** : acceptée.

## Contexte

Les bibliothèques d'évaluation (Ragas, DeepEval), les SDK des fournisseurs et l'outillage IA sont d'abord en Python. Un premier découpage proposait une API .NET et des workers Python.

## Décision

Backend entièrement en Python 3.13 : FastAPI, Pydantic, SQLAlchemy 2 asynchrone, Alembic, httpx ; dépendances et espace de travail gérés par uv.

## Conséquences

- Une seule pile, un seul jeu de contrats (Pydantic) entre l'API, le moteur, les workers et la CLI.
- Les évaluations passent leur temps à attendre des fournisseurs : l'asynchrone (asyncio) convient, le calcul reste léger.
- Abandon de l'hybride .NET + Python : il aurait dupliqué les contrats et la sérialisation pour un gain nul ici. Le projet DevPulse porte déjà la partie .NET du portfolio.
