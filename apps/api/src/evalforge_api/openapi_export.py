"""Écrit le contrat OpenAPI, d'où le front génère ses types : `python -m evalforge_api.openapi_export <fichier>`.

Aucune base ni clé réelle n'est nécessaire : seules les routes et les schémas sont lus. Les clés sont tirées au
hasard à chaque export et ne servent à rien d'autre.
"""

import json
import secrets
import sys
from pathlib import Path

from cryptography.fernet import Fernet

from evalforge_api.core.config import Settings
from evalforge_api.main import create_app


def main() -> None:
    settings = Settings(secret_key=Fernet.generate_key().decode(), jwt_secret_key=secrets.token_urlsafe(32))
    schema = create_app(settings).openapi()
    Path(sys.argv[1]).write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
