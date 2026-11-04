from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Ce dont les migrations ont besoin : rien de secret au-delà de l'URL."""

    model_config = SettingsConfigDict(env_prefix="EVALFORGE_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://evalforge:evalforge@localhost:5435/evalforge"


class Settings(DatabaseSettings):
    # Clé Fernet qui chiffre les clés d'API des fournisseurs en base. En production : Secrets Manager / KMS.
    secret_key: SecretStr
    provider_timeout_seconds: float = 60.0
