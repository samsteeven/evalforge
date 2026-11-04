# ADR-010 : React et TypeScript pour l'interface

**État** : acceptée, mise en œuvre à l'incrément 2. Remplace le choix initial de Vue 3.

## Contexte

Le blueprint prévoyait Vue 3. Le choix a été revu avant d'écrire la moindre ligne de front : DevPulse, l'autre projet du portfolio, montre déjà Vue 3.

## Décision

React avec TypeScript et Vite. Pressenti pour l'incrément 2, à confirmer à ce moment-là : React Router, TanStack Query pour les appels à l'API, Zod pour valider les réponses, Tailwind CSS, une bibliothèque de graphiques pour les comparaisons de modèles, Vitest et Playwright.

## Conséquences

- Aucun impact sur le backend : l'interface ne consomme que l'API REST documentée par OpenAPI (types TypeScript générables depuis `/openapi.json`).
- La progression d'un run en direct passera par Server-Sent Events, comme prévu.
