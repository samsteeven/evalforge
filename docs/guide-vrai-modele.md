# Tester EvalForge avec un vrai modèle

Par défaut, les exemples, les tests et la CI utilisent un **faux fournisseur** (`scripts/fake_llm.py`) et des **politiques d'agent écrites à la main**. C'est voulu : les résultats sont reproductibles et la CI ne dépend d'aucune clé. Mais cela prouve la mécanique de mesure, pas le comportement d'un vrai modèle. Ce guide explique comment brancher un vrai modèle, et ce que cela permet de vérifier.

## 1. Choisir un modèle

| Option | Coût | Adresse | Remarques |
|---|---|---|---|
| **Ollama** (local) | gratuit | `http://localhost:11434/v1` | il faut un modèle qui sait appeler des outils pour l'agent (par exemple `qwen2.5:3b`, `llama3.2:3b`) ; un petit modèle se trompera davantage |
| **DeepSeek** | quelques centimes pour ces exemples | `https://api.deepseek.com/v1`, modèle `deepseek-chat` | appels de fonctions pris en charge ; bon candidat pour le juge |
| Tout fournisseur compatible OpenAI | selon le fournisseur | son adresse `/v1` | idem |

Ollama :

```powershell
setx OLLAMA_MODELS D:\ollama\models     # facultatif : ranger les modèles hors de C:
ollama pull qwen2.5:3b
```

Une clé d'API reste **dans une variable d'environnement**, jamais dans un fichier du dépôt :

```powershell
$env:DEEPSEEK_API_KEY = "..."
```

Ollama écoute sur une adresse locale : elle est refusée par défaut (protection SSRF, [ADR-017](architecture/adr/0017-protection-ssrf.md)). L'autoriser explicitement : `allow_private_urls: true` dans le YAML de la CLI, ou `EVALFORGE_ALLOW_PRIVATE_PROVIDER_URLS=true` pour la plateforme.

## 2. Un modèle et le juge LLM (CLI)

Copier `examples/support-faq/evalforge.yaml` (par exemple en `reel.yaml`, à côté) et remplacer les faux modèles :

```yaml
models:
  - name: Qwen 3B
    base_url: http://localhost:11434/v1
    model: qwen2.5:3b
  - name: DeepSeek
    base_url: https://api.deepseek.com/v1
    model: deepseek-chat
    api_key_env: DEEPSEEK_API_KEY
    # Prix à reprendre de la page tarifaire du fournisseur : sans prix, le coût reste « inconnu ».
    pricing: { input_per_million_usd: 0.27, output_per_million_usd: 1.10 }

judge:
  name: Juge
  base_url: https://api.deepseek.com/v1   # un juge au moins aussi fort que les modèles évalués
  model: deepseek-chat
  api_key_env: DEEPSEEK_API_KEY

allow_private_urls: true                  # seulement pour Ollama
```

```powershell
uv run evalforge run examples/support-faq/reel.yaml --report rapport.html --json reel.json
```

À quoi s'attendre : `exact_match` baisse fortement (un vrai modèle reformule), `judge_correctness` devient la métrique utile. Relire les justifications du juge dans `rapport.html`. Les métriques Ragas et DeepEval s'utilisent de la même façon, après `uv sync --group ragas` (ou `deepeval`).

## 3. L'agent de support avec un vrai modèle

L'agent prend son modèle dans l'environnement :

```powershell
$env:SUPPORT_AGENT_LLM_BASE_URL = "https://api.deepseek.com/v1"   # ou http://localhost:11434/v1
$env:SUPPORT_AGENT_LLM_MODEL    = "deepseek-chat"                 # ou qwen2.5:3b
$env:SUPPORT_AGENT_LLM_API_KEY  = $env:DEEPSEEK_API_KEY            # inutile pour Ollama
uv run evalforge-support-agent                                     # port 8200
```

Dans un autre terminal :

```powershell
uv run evalforge run examples/support-agent/evalforge.yaml --report agent.html --json agent-reel.json
uv run evalforge run examples/support-agent/securite.yaml --report securite.html
```

- **Trajectoires** : le modèle appelle-t-il `find_order` puis `get_return_policy`, avec les bons arguments ? Combien d'étapes inutiles ?
- **Injections de prompt** : révèle-t-il ses consignes (la garde de sortie doit le rattraper, voir `guarded` dans la trajectoire) ? Crée-t-il le ticket forcé, suit-il l'instruction cachée dans la note de la commande `CMD-1045` ?

Ne pas utiliser `examples/support-agent/baseline.json` : il vient du faux fournisseur, la comparaison n'aurait pas de sens. Le premier résultat réel (`agent-reel.json`) sert de baseline aux suivants :

```powershell
uv run evalforge run examples/support-agent/evalforge.yaml --baseline agent-reel.json
```

Attention : un vrai modèle n'est pas déterministe, même à température 0. Rejouer deux fois la même configuration montre l'écart « normal » ; une régression ne se juge qu'au-delà de cet écart, et avec plus de cas que les exemples (5 cas ne prouvent rien, [ADR-023](architecture/adr/0023-detection-de-regression.md)).

## 4. Par l'interface

Démarrer l'API, le worker et le front (voir le README), puis dans un projet :

- **Modèles** : type « Compatible OpenAI » (ou « Ollama »), adresse, identifiant du modèle, clé (chiffrée en base, jamais réaffichée), prix avec sa source ;
- **Agent** : type « Agent EvalForge », adresse `http://127.0.0.1:8200` (l'agent démarré comme au point 3) ;
- **Jeu de données** : importer `examples/support-faq/support-faq.csv` ou `examples/support-agent/demandes-clients.json`.

## 5. Ce que cela vérifie, et ce qui reste à faire

| Question | Comment |
|---|---|
| La plateforme fonctionne-t-elle avec un vrai fournisseur (réponses, tokens, coût, erreurs) ? | points 2 et 4 |
| Un vrai modèle choisit-il les bons outils ? | point 3, métriques de trajectoire |
| Résiste-t-il aux injections ? | point 3, `securite.yaml` |
| **Le juge est-il fiable ?** | noter soi-même une vingtaine de réponses (de 1 à 5, avec la même grille) et comparer aux notes du juge ; tant que ce n'est pas fait, ses notes restent des avis ([ADR-019](architecture/adr/0019-juge-llm.md)) |

Les résultats obtenus ainsi peuvent compléter le tableau « vérifié / pas vérifié » du README, en précisant le modèle, la date et le nombre de cas.
