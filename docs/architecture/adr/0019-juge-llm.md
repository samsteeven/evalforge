# ADR-019 : un juge LLM natif, à grilles versionnées

**État** : acceptée.

## Contexte

Les métriques déterministes (correspondance, inclusion) ratent une réponse juste mais formulée autrement. Un modèle « juge » sait apprécier le sens, mais il a ses biais : sensibilité à la longueur, au style, aux instructions glissées dans la réponse, et il peut se tromper.

## Décision

- Un évaluateur `LLMJudge` dans le moteur, appelé à travers la passerelle LLM : mêmes délais, reprises, protection SSRF et mesure des tokens que les modèles évalués.
- Des **grilles écrites et versionnées** (`correctness-v1`, `faithfulness-v1`) : note entière de 1 à 5 avec une phrase de justification, ramenée à 0..1. Seuil de réussite : 4 sur 5.
- Température 0. Consigne d'ignorer longueur, style et toute instruction contenue dans la réponse évaluée.
- Chaque note garde la grille et sa version, le modèle juge réellement utilisé (nom renvoyé par le fournisseur), la note brute et les tokens du juge.
- Une réponse du juge sans JSON, ou une note hors grille, rend la métrique **indisponible** : jamais 0, et le run continue.
- Le modèle juge est choisi par expérience et figé dans chaque run, comme les modèles comparés.

## Pourquoi pas seulement Ragas ou DeepEval

Leurs métriques (fidélité par décomposition en affirmations, pertinence…) sont plus fines, et elles arriveront derrière des adaptateurs. Mais ce sont des chaînes de plusieurs appels, aux formats imposés, dont les versions évoluent vite (Ragas 0.4.3 ne s'importe pas avec la dernière version de `langchain-community`). Le juge natif reste simple, lisible et sous contrôle ; les deux approches se comparent sur les mêmes runs.

## Conséquences

- L'accord du juge avec des évaluateurs humains n'est pas mesuré : c'est l'étape suivante (annotation humaine, taux d'accord).
- Le coût du juge est enregistré par note, pas encore additionné au coût du run.
- Changer une grille, c'est créer une nouvelle version : les notes anciennes restent comparables entre elles.
