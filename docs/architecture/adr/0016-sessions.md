# ADR-016 : sessions par jeton d'accès court et jeton de renouvellement à usage unique

**État** : acceptée. Remplace l'ADR-015 (espace par en-tête sans authentification).

## Contexte

Le front est une application React qui appelle l'API. Il faut une session qui survive au rechargement de la page sans exposer de jeton au JavaScript, et qui limite les dégâts d'un jeton volé.

## Décision

- **Mots de passe** hachés avec Argon2. Un e-mail inconnu coûte le même calcul qu'un mauvais mot de passe : la réponse ne révèle pas si le compte existe.
- **Jeton d'accès** JWT HS256 de 15 minutes, envoyé dans `Authorization`. HS256 suffit : l'API est seule à émettre et à vérifier ses jetons (DevPulse, avec plusieurs services, a besoin de RS256). Côté navigateur, il ne vit qu'en mémoire.
- **Jeton de renouvellement** aléatoire, dans un cookie `HttpOnly`, `SameSite=Strict`, limité à `/api/v1/auth`. Stocké haché en base. Il est remplacé à chaque usage ; un jeton déjà remplacé qui revient signale un vol : toute sa famille (la connexion) est révoquée.
- **Anti-CSRF** : les routes qui lisent ce cookie exigent l'en-tête `X-Requested-With: EvalForge`, qu'un formulaire d'un autre site ne peut pas ajouter.
- **Espace de travail et rôle** relus en base à chaque requête (en-tête `X-Workspace-Id` + appartenance) plutôt que placés dans le jeton : retirer un membre prend effet immédiatement.

## Conséquences

- Le front renouvelle la session une minute avant l'expiration, et au chargement de la page. Un seul renouvellement à la fois par onglet : deux requêtes simultanées présenteraient le même jeton, et la seconde serait prise pour un rejeu.
- Deux onglets qui renouvellent exactement au même instant déconnectent l'utilisateur (le cookie est partagé, le cas reste rare). Une période de grâce de quelques secondes le corrigerait, au prix d'une fenêtre de rejeu.
- Pas de révocation d'un jeton d'accès déjà émis : il reste valable 15 minutes au plus, mais il ne donne plus accès à un espace dont l'utilisateur a été retiré.
