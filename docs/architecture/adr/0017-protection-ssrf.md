# ADR-017 : protection SSRF au moment de la connexion

**État** : acceptée.

## Contexte

L'adresse d'un fournisseur de modèle est saisie par l'utilisateur, et c'est le serveur qui l'appelle. Sans contrôle, `http://169.254.169.254/` (métadonnées cloud, identifiants AWS) ou un service interne deviennent joignables depuis Internet. Vérifier l'adresse seulement à l'enregistrement ne suffit pas : un nom DNS peut répondre une adresse publique au contrôle, puis une adresse interne à la connexion (DNS rebinding).

## Décision

- **À l'enregistrement** : refus immédiat de ce qui se voit sans DNS (`localhost`, adresses IP privées, de bouclage, lien local…), avec un message clair.
- **À chaque appel** : un transport httpx (`SafeTransport`) résout le nom, refuse si **une** des adresses n'est pas publique, puis se connecte à l'adresse vérifiée. Le nom d'origine reste dans l'en-tête `Host` et dans le SNI : le certificat TLS est toujours contrôlé contre le nom.
- « Publique » suit `ipaddress.is_global` (exclut privé, bouclage, lien local, partagé 100.64/10, réservé…), plus le multicast, et une IPv4 déguisée en IPv6 (`::ffff:127.0.0.1`) est ramenée à son IPv4.
- Pas de redirection suivie. Une destination interdite n'est jamais rejouée.
- `EVALFORGE_ALLOW_PRIVATE_PROVIDER_URLS=true` lève la protection pour un modèle local (Ollama) ou le faux fournisseur de test : jamais en production.

## Conséquences

- Le contrôle est dans la passerelle LLM, pas dans les routes : tout futur appel sortant (juge LLM, webhooks) passera par le même transport.
- Une liste d'adresses autorisées par espace de travail reste possible au-dessus de ce contrôle.
