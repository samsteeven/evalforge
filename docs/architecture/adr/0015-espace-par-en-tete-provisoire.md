# ADR-015 : espace de travail par en-tête (provisoire)

**État** : provisoire, remplacée à l'incrément 2 par l'authentification.

## Contexte

Toutes les données appartiennent à un espace de travail dès le premier jour : ajouter l'isolation après coup oblige à reprendre chaque requête. L'authentification, elle, n'est pas dans la première tranche.

## Décision

Une dépendance FastAPI, `current_workspace_id`, fournit l'espace courant à chaque route ; elle le lit pour l'instant dans l'en-tête `X-Workspace-Id` et vérifie qu'il existe. Chaque requête filtre sur cet espace, et une ressource d'un autre espace répond 404, comme si elle n'existait pas.

## Conséquences

- L'isolation des requêtes est réelle et testée ; quand l'espace viendra de l'appartenance de l'utilisateur authentifié, seule cette dépendance change.
- **Aucune sécurité** tant que c'est un en-tête : connaître l'identifiant d'un espace suffit. Cette version ne doit pas être exposée.
