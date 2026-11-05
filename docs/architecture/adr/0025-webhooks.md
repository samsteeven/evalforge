# ADR-025 : webhooks signés, envoyés par les workers à partir des événements de fin de run

**État** : acceptée.

## Contexte

Une CI, une messagerie d'équipe ou un outil interne doit apprendre qu'un run est terminé, et s'il régresse, sans interroger l'API en boucle. L'envoi part vers une adresse saisie par un utilisateur, qui peut être lente, en panne, ou viser le réseau interne.

## Décision

- **Abonnement par projet** (`POST /projects/{id}/webhooks`, administrateurs seulement : l'URL peut porter un jeton du destinataire) à `evaluation.completed` et/ou `evaluation.failed`. Adresse vérifiée à l'enregistrement et à chaque envoi (même protection SSRF que les fournisseurs), redirections non suivies.
- **Secret de signature** généré par EvalForge, chiffré en base (Fernet), montré une seule fois. Chaque appel porte `X-EvalForge-Timestamp` et `X-EvalForge-Signature: sha256=HMAC(secret, "timestamp.corps")`. Le destinataire refuse une signature fausse ou un horodatage de plus de 5 minutes (rejeu) ; `evalforge_api.modules.webhooks.delivery.verify` fait les deux, en temps constant.
- **Aucun envoi dans la transaction métier** : la fin du run écrit déjà son événement dans l'outbox ([ADR-022](0022-execution-asynchrone.md)). Une file `<préfixe>.webhooks`, liée aux deux événements de fin, est consommée par les workers.
- **Charge utile** relue en base au moment de l'envoi : état du run, taux de réussite par modèle et par métrique, et régression face à la baseline du projet (métriques régressées, taux avant et après, p-valeur), ou `null` sans baseline.
- **Une livraison par abonnement et par événement** (`webhook_deliveries`, unique sur le couple) : 2xx livrée ; 4xx sauf 408 et 429, refus définitif ; 5xx, 408, 429, délai dépassé ou réseau, nouvelle tentative par `<préfixe>.webhooks.retry` (délai croissant, 5 tentatives par défaut). Une reprise ne renvoie que les livraisons encore en attente. L'historique (tentatives, dernier code, dernière erreur) est consultable dans l'interface.

## Conséquences

- Livraison « au moins une fois » : un worker arrêté entre l'envoi réussi et l'enregistrement renverra l'appel. L'en-tête `X-EvalForge-Delivery` (stable entre les tentatives) permet au destinataire de dédoublonner.
- Les envois d'un même événement sont séquentiels : un destinataire lent (10 s de délai au plus) retarde les autres abonnements du projet.
- L'intégration avec Slack, l'e-mail ou GitHub reste hors d'EvalForge (n8n, ou un petit service qui vérifie la signature), comme prévu par la vision.
