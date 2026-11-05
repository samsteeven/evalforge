# ADR-021 : Ragas et DeepEval derrière la passerelle EvalForge, en dépendances optionnelles

**État** : acceptée. Met en œuvre l'ADR-008.

## Contexte

Ragas et DeepEval apportent des métriques reconnues (fidélité par décomposition en affirmations, pertinence de la réponse). Constats faits sur leurs versions actuelles, avant d'écrire l'adaptateur :

- Ragas 0.4.3 ne s'importe pas avec `langchain-community` 0.4, qui a retiré un module dont il dépend.
- Installées ensemble, les deux bibliothèques se contraignent : DeepEval redescend de 4.3 à 2.9.
- Les deux envoient des statistiques d'usage par défaut.
- Les deux acceptent un « modèle » fourni par l'appelant : Ragas appelle `agenerate(prompt, schéma)`, DeepEval `a_generate(prompt, schema=…)`, et toutes deux attendent un objet Pydantic conforme.

## Décision

- **Passerelle EvalForge plutôt que leurs clients.** Un adaptateur par bibliothèque (`GatewayRagasLLM`, `GatewayDeepEvalModel`) répond à leurs demandes en passant par `llm_gateway`. Le schéma JSON est joint au prompt, la réponse validée, redemandée une fois si elle n'est pas conforme. Les appels gardent ainsi délais, reprises, protection SSRF et comptage des tokens (nombre d'appels et tokens par mesure).
- **Le modèle juge de l'expérience** sert aussi à ces métriques : un seul modèle évaluateur à configurer et à figer dans le run.
- **Dépendances optionnelles et en conflit déclaré** (`uv sync --group ragas` ou `--group deepeval`), `langchain-community` bornée sous 0.4. Une métrique dont la bibliothèque n'est pas installée n'est pas proposée (`/metrics`, validation des expériences et de la CLI).
- **Télémétrie coupée** avant tout import (`RAGAS_DO_NOT_TRACK`, `DEEPEVAL_TELEMETRY_OPT_OUT`).
- **Métriques** : `ragas_faithfulness`, `deepeval_faithfulness`, `deepeval_answer_relevancy`. Seuil de réussite commun : 0,75. Un NaN de Ragas (aucune affirmation à vérifier) devient « indisponible », pas 0.
- **Tests contre les vraies bibliothèques**, avec un faux modèle qui lit le schéma demandé et répond un objet conforme : on vérifie leurs prompts, leurs schémas et leur calcul du score. Un job de CI par bibliothèque.

## Conséquences

- Trois mesures de la fidélité (juge EvalForge, Ragas, DeepEval) se comparent sur les mêmes runs.
- Une même plateforme ne propose qu'une des deux bibliothèques à la fois, tant que leurs dépendances ne s'accordent pas.
- Le format JSON joint au prompt suppose un modèle capable de le suivre ; les sorties structurées natives (`response_format`) des fournisseurs qui les ont sont une amélioration possible.
- Les métriques Ragas qui demandent des plongements vectoriels (pertinence de la réponse, par exemple) ne sont pas branchées : la passerelle ne gère pas encore les embeddings.
