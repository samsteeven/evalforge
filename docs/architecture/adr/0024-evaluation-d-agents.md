# ADR-024 : agents évalués sur leur trajectoire, à travers un contrat HTTP

**État** : acceptée.

## Contexte

Un agent qui appelle des outils peut donner une réponse finale correcte en ayant fait n'importe quoi : transféré le client à un humain sans raison, appelé un outil avec un argument mal formé, inventé une règle au lieu de la consulter. Juger la réponse seule ne voit rien de tout cela. Il fallait aussi un agent à évaluer, sans dépendre d'un framework particulier ni d'une clé d'API.

## Décision

- **Un agent de démonstration réel** (`apps/support_agent`) : quatre outils (recherche de commande, politique de retour, création de ticket, transfert à un conseiller), arguments validés par Pydantic, boucle d'appels de fonctions au format OpenAI à travers la passerelle EvalForge (`ToolSpec`, `ToolCall`), budget d'étapes. Chaque étape est tracée : outil, arguments, état (`success`, `invalid_arguments`, `unknown_tool`, `error`), résultat, latence. Un argument invalide est renvoyé au modèle comme une erreur d'outil, pas comme une exception : le modèle peut se corriger, et la trajectoire garde la trace de l'erreur.
- **Contrat HTTP minimal** pour tout agent évalué : `POST {base_url}/v1/agent/run {"message"}` → `{"output", "trajectory", "stop_reason", "model", "input_tokens", "output_tokens"}`. EvalForge ne connaît ni LangGraph, ni CrewAI, ni le code de l'agent : n'importe quel agent qui expose ce contrat s'évalue de la même façon. Dans la plateforme, c'est un type de fournisseur (`evalforge_agent`) ; dans la CLI, `kind: agent`. L'appel passe par le client à protection SSRF, comme pour un fournisseur.
- **Cinq métriques de trajectoire**, déterministes : `tool_selection` (outils attendus appelés avec succès), `tool_order` (dans l'ordre, d'autres appels intercalés tolérés), `tool_arguments` (arguments attendus retrouvés, comparaison insensible à la casse et aux espaces), `step_efficiency` (aucun appel hors attendu, répété ou raté), `task_completion` (une réponse finale, sans budget d'étapes épuisé). Le contenu de la réponse se mesure à part, avec les métriques habituelles (`contains_expected`, juge). Seuls les appels réussis comptent comme faits. Sans attendu ou sans trajectoire : métrique indisponible, pas 0.
- **Trajectoire enregistrée** avec chaque résultat (JSONB), affichée dans l'interface et le rapport HTML.
- **Un agent ne peut pas être juge** : refusé par l'API et par la CLI.
- **Porte de qualité d'agent en CI** : le vrai processus de l'agent, piloté par le faux fournisseur, doit passer la porte et rester au niveau de la baseline committée ; le même agent piloté par un modèle « négligent » doit être bloqué (code 1).

## Conséquences

- Le pilote négligent obtient 100 % à `task_completion` et 0 à 20 % sur les métriques de trajectoire : c'est la démonstration que la réponse seule ne suffit pas.
- Le modèle qui pilote l'agent en démonstration est une politique écrite à la main (`scripts/fake_llm.py`) : elle prouve la mécanique (appels d'outils, reprise d'erreur, mesures), pas la qualité d'un vrai modèle. Avec un vrai fournisseur, seules `SUPPORT_AGENT_LLM_BASE_URL`, `_MODEL` et `_API_KEY` changent.
- Les attendus sont des chemins exacts : un agent qui atteint le but par un autre chemin valide est pénalisé. Des trajectoires alternatives acceptables seraient l'étape suivante.
- L'agent tourne dans son propre processus, jamais dans l'API ni dans le worker.
