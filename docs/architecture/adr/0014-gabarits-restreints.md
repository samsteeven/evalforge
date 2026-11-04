# ADR-014 : gabarits de prompt limités à `{{ variable }}`

**État** : acceptée.

## Contexte

Le gabarit d'un prompt est saisi par l'utilisateur et rendu côté serveur pour chaque cas. `str.format` donne accès aux attributs des objets (`{question.__class__}`) ; un moteur complet (Jinja) ajoute boucles, filtres et un bac à sable à maintenir.

## Décision

Seule la substitution `{{ nom }}` est reconnue, avec pour valeurs les champs `input` du cas et `context` (les passages, un par ligne). Une valeur non textuelle est insérée en JSON. Toute autre accolade reste du texte. Une variable absente du cas fait échouer ce cas, avec un message qui la nomme, sans arrêter le run.

## Conséquences

- Aucun accès au-delà des données du cas.
- Pas de conditions ni de boucles : un prompt qui en aurait besoin se construit en amont, dans les champs du cas.
- Les variables d'un prompt sont calculables à l'enregistrement (`variables` dans la réponse de l'API).
