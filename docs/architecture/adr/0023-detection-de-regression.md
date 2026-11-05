# ADR-023 : baseline de projet et détection de régression appariée

**État** : acceptée.

## Contexte

Le cœur de la démonstration du blueprint : modifier un prompt, relancer, savoir si c'est pire. Comparer deux taux de réussite globaux ne suffit pas : ils peuvent rester égaux alors que des cas différents ont cassé et d'autres été réparés, et un écart sur 5 cas n'a pas le même poids que sur 500.

## Décision

- **Baseline par projet** : un run terminé, adopté par un membre (et au-dessus), avec une tolérance (baisse acceptée, 2 points par défaut).
- **Comparaison appariée** dans le moteur (`evalforge_engine.comparison`), utilisée par l'API et par la CLI : même empreinte de jeu de données exigée, modèles appariés par leur nom, métriques communes aux deux runs. Pour chaque métrique : taux avant / après, écart, cas cassés et réparés.
- **Test de McNemar exact** sur les cas discordants : il dit si le déséquilibre cassés / réparés dépasse ce que le hasard expliquerait (seuil 5 %). Il est affiché à côté de la décision, il ne la remplace pas : la régression est décidée par la tolérance, la significativité dit si on peut s'y fier.
- Une métrique indisponible d'un côté n'entre pas dans l'appariement.
- **CLI** : `--baseline rapport.json` compare à une évaluation précédente et échoue (code 1) en cas de régression.

## Conséquences

- Sur 5 cas, même 5 cas cassés sur 5 donnent p = 0,0625 : « régression nette, non prouvée ». C'est voulu : le petit jeu de démonstration ne suffit pas à conclure, et l'outil le dit.
- La régression ne porte que sur les métriques de qualité ; latence et taux de réussite sont affichés sans seuil.
- Comparer deux jeux de données différents est refusé, plutôt que de comparer des moyennes sans rapport.
