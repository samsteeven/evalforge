from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Ce dont les migrations ont besoin : rien de secret au-delà de l'URL."""

    model_config = SettingsConfigDict(env_prefix="EVALFORGE_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://evalforge:evalforge@localhost:5435/evalforge"


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
