from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Ce dont les migrations ont besoin : rien de secret au-delà de l'URL."""

    model_config = SettingsConfigDict(env_prefix="EVALFORGE_", env_file=".env", extra="ignore")

    # 127.0.0.1 plutôt que localhost : sous Windows, localhost essaie d'abord ::1, où Docker n'écoute pas, et chaque
    # nouvelle connexion perd 2 s avant de se rabattre sur IPv4 (mesuré par le test de charge, ADR-026).
    database_url: str = "postgresql+asyncpg://evalforge:evalforge@127.0.0.1:5435/evalforge"


class Settings(DatabaseSettings):
    # Clé Fernet qui chiffre les clés d'API des fournisseurs en base. En production : Secrets Manager / KMS.
    secret_key: SecretStr
    # Signature des jetons d'accès (HS256 : l'API est seule à les émettre et à les vérifier).
    jwt_secret_key: SecretStr
    access_token_minutes: int = 15
    refresh_token_days: int = 14
    # Cookie de renouvellement en HTTPS seulement ; à désactiver uniquement en développement local (http).
    cookie_secure: bool = True
    # Autorise les adresses privées pour les fournisseurs (Ollama sur le poste) : jamais en production.
    allow_private_provider_urls: bool = False
    provider_timeout_seconds: float = 60.0

    # Connexions gardées ouvertes par processus, et surplus temporaire en pointe (ouvert puis refermé).
    database_pool_size: int = 20
    database_max_overflow: int = 10

    # Messagerie : l'API écrit dans l'outbox et publie ; les workers consomment.
    rabbitmq_url: str = "amqp://evalforge:evalforge@127.0.0.1:5672/"
    # Préfixe des échanges et files : plusieurs environnements (ou tests) partagent un courtier sans se mélanger.
    rabbitmq_prefix: str = "evalforge"
    outbox_interval_seconds: float = 2.0
    worker_prefetch: int = 2
    # Bail d'un run : sans battement pendant cette durée, un autre worker peut le reprendre.
    run_lease_seconds: float = 60.0
    max_attempts: int = 3
    retry_base_delay_seconds: float = 5.0

    # Webhooks : envoyés par les workers, avec la même protection SSRF que les fournisseurs.
    allow_private_webhook_urls: bool = False
    webhook_timeout_seconds: float = 10.0
    webhook_max_attempts: int = 5
    webhook_retry_base_delay_seconds: float = 30.0
