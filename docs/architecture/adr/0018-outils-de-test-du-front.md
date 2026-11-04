# ADR-018 : Vitest et Cypress pour le front

**État** : acceptée.

## Décision

- **Vitest** et Testing Library pour les tests unitaires des composants et du formatage (coût inconnu, métrique indisponible).
- **Cypress** (avec `@testing-library/cypress`) pour le parcours de bout en bout dans un vrai navigateur. `start-server-and-test` démarre le faux fournisseur, l'API et le front, puis lance Cypress ; la CI fait de même après les tests unitaires.
- Les sélecteurs passent par les rôles et les libellés accessibles (`findByRole`, `findByLabelText`) : un test qui ne trouve pas un champ par son libellé signale aussi un défaut d'accessibilité.

## Pourquoi Cypress plutôt que Playwright

DevPulse, l'autre projet du portfolio, est testé avec Playwright. Cypress est demandé dans une partie des offres : chaque projet montre ainsi un outil différent. Les deux couvrent le besoin ici (un parcours, un navigateur).

## Conséquences

- Cypress ne pilote pas plusieurs onglets ni plusieurs domaines dans un même test : suffisant pour EvalForge, où tout passe par la même origine.
- La vue est fixée à 1 280 × 800, la taille de la fenêtre du navigateur sans affichage : au-delà, les captures sont coupées.
