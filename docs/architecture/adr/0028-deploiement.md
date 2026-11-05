# ADR-028 : images non root, Kubernetes compatible OpenShift, Ansible, Terraform pour AWS

**État** : acceptée.

## Contexte

Les offres visées demandent de livrer une application sur OpenShift (souvent par Ansible) ou sur un cloud public. OpenShift est plus strict qu'un Kubernetes par défaut : il attribue à chaque conteneur un UID arbitraire (groupe 0) et refuse tout ce qui demande des privilèges. Un manifeste qui « marche sur kind » peut échouer sur OpenShift ; il fallait reproduire ces contraintes en CI sans cluster OpenShift.

## Décision

- **Deux images** : `evalforge-backend` (API, worker, migrations, agent de démonstration, CLI ; la commande change) et `evalforge-frontend` (nginx sans privilèges sur 8080, qui relaie `/api` : une seule origine, comme en développement). Utilisateur non root, fichiers du groupe 0 avec les droits du propriétaire (UID arbitraire possible), racine en lecture seule, `/tmp` et la configuration nginx rendue en volumes temporaires. Versions de base épinglées. `docker compose --profile app` lance toute la plateforme depuis les images.
- **Kustomize** : une base (API, worker, front, Job de migration, NetworkPolicies, PodDisruptionBudget) sans secret ni dépendance ; des composants (`dependencies` : PostgreSQL et RabbitMQ de test sous leurs UID ; `demo` : faux fournisseur et agent) ; deux overlays (`kind`, `openshift` avec une Route TLS). Chaque pod : `runAsNonRoot`, `seccompProfile: RuntimeDefault`, aucune capacité, pas d'escalade, pas de jeton de compte de service, pas de variables de liens de services (`SUPPORT_AGENT_PORT=tcp://…` cassait l'agent).
- **OpenShift reproduit en CI** sur kind : niveau `restricted` imposé à l'espace de noms (un pod root est refusé, vérifié), UID `1000680000` et groupe 0 imposés aux conteneurs EvalForge, NetworkPolicies vérifiées par des tentatives de connexion (le faux fournisseur ne joint pas l'API, le worker joint l'agent).
- **Ansible** (`deploy/ansible`, collection `kubernetes.core`) : secrets depuis l'inventaire (coffre ansible-vault pour un vrai environnement), dépendances de test prêtes, Job de migration supprimé puis rejoué (son modèle est immuable), application, attente de disponibilité. `ansible-lint` profil production. En CI : déploiement, redéploiement, puis un run d'agent de bout en bout à travers le front.
- **Terraform pour AWS** (`deploy/terraform/aws`) : VPC sur deux zones, ALB (HTTPS si un certificat est fourni), ECS Fargate (front, API via Service Connect, worker, tâche de migration), RDS PostgreSQL et Amazon MQ chiffrés et privés, Secrets Manager, ECR à étiquettes immuables, sorties limitées à HTTPS, la base et le courtier. Validé en CI (`fmt`, `validate`) et analysé par Trivy ; **jamais appliqué** : pas de compte AWS utilisé.

## Conséquences

- Les secrets ne sont dans aucun manifeste versionné. Ceux de Terraform passent par son état : à stocker chiffré (S3 avec chiffrement, accès restreint).
- PostgreSQL et RabbitMQ du composant `dependencies` sont en mémoire et à une réplique : test seulement. En production, services managés ou opérateurs.
- La compatibilité OpenShift est démontrée par ses contraintes, pas par un vrai cluster OpenShift : les SCC personnalisées, les quotas et les Routes ne sont pas testés.
- Trivy est en mode rapport : une vulnérabilité publiée dans une image de base ne bloque pas une modification sans rapport. Les exceptions de configuration sont commentées dans le code (`trivy:ignore` avec la raison).
