"""Mots de passe, jetons d'accès et jetons de renouvellement."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

ROLES = ("viewer", "member", "admin", "owner")
ROLE_RANK = {role: rank for rank, role in enumerate(ROLES)}


class Passwords:
    def __init__(self) -> None:
        self._hasher = PasswordHasher()
        # Vérifiée quand l'e-mail est inconnu : la réponse prend le même temps qu'un mauvais mot de passe.
        self._dummy = self._hasher.hash(secrets.token_urlsafe(16))

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str | None, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash or self._dummy, password) and password_hash is not None
        except (VerificationError, InvalidHashError):
            return False


class InvalidTokenError(Exception):
    pass


class AccessTokens:
    def __init__(self, secret: str, minutes: int) -> None:
        self._secret = secret
        self.lifetime = timedelta(minutes=minutes)

    def issue(self, user_id: uuid.UUID, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        claims = {"sub": str(user_id), "iat": now, "exp": now + self.lifetime, "typ": "access"}
        return jwt.encode(claims, self._secret, algorithm="HS256")

    def verify(self, token: str) -> uuid.UUID:
        try:
            claims = jwt.decode(token, self._secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
            if claims.get("typ") != "access":
                raise InvalidTokenError("type de jeton inattendu")
            return uuid.UUID(claims["sub"])
        except (jwt.PyJWTError, ValueError) as error:
            raise InvalidTokenError(str(error)) from None


def new_refresh_token() -> tuple[str, str]:
    """Le jeton n'est stocké que haché : une fuite de la base ne donne aucune session."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
