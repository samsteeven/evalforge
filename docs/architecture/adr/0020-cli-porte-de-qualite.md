# ADR-020 : une CLI qui partage le moteur, et sert de porte de qualité

**État** : acceptée.

## Contexte

Une équipe veut évaluer un changement de prompt ou de modèle avant de le déployer, dans sa CI, sans dépendre d'une plateforme hébergée. Réimplémenter l'évaluation dans un script donnerait des chiffres différents de ceux de la plateforme.

## Décision

- `apps/cli` (Typer) : `evalforge run <config.yaml>` lit les cas (JSON ou CSV), appelle les modèles et le juge, calcule les mêmes agrégats et produit le même rapport HTML que la plateforme, en n'utilisant que `evaluation_engine` et `llm_gateway`.
- Configuration YAML validée strictement (clé inconnue = erreur) ; chemins relatifs au fichier ; clés d'API lues dans des variables d'environnement nommées, jamais écrites dans le fichier.
- `thresholds` : taux de réussite minimal par métrique, exigé de chaque modèle. Codes de sortie : 0 succès, 1 porte non franchie, 2 configuration ou données invalides. Une métrique sans aucune mesure ne franchit pas la porte.
- Même contrôle des adresses que la plateforme : `allow_private_urls: true` explicite pour un modèle local.

## Conséquences

- Les chiffres d'un run local et d'un run de la plateforme sont calculés par le même code.
- La CLI ne parle pas encore à l'API (pas de « lancer un run distant ») : prévu avec les webhooks et le contrôle qualité en CI (incrément 5).
