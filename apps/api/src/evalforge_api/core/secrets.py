"""Chiffrement applicatif des secrets stockés en base (clés d'API des fournisseurs)."""

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key)
        except ValueError:
            raise ValueError("EVALFORGE_SECRET_KEY doit être une clé Fernet (Fernet.generate_key())") from None

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            raise ValueError("secret illisible : la clé de chiffrement a changé ?") from None
