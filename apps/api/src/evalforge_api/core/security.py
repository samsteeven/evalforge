"""Mots de passe, jetons d'accès et jetons de renouvellement."""

import hashlib
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import anyio
import anyio.to_thread
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

ROLES = ("viewer", "member", "admin", "owner")
ROLE_RANK = {role: rank for rank, role in enumerate(ROLES)}


class Passwords:
    """Argon2id : coûteux par construction (temps et 64 Mio de mémoire par calcul).

    Les méthodes asynchrones calculent hors de la boucle d'événements (sinon chaque inscription gèle l'API), et au
    plus `max_concurrent` à la fois : une rafale d'inscriptions ou de connexions attend son tour au lieu de demander
    64 Mio par requête simultanée. Mesuré par le test de charge (ADR-026).
    """

    def __init__(self, max_concurrent: int | None = None) -> None:
        self._hasher = PasswordHasher()
        # Vérifiée quand l'e-mail est inconnu : la réponse prend le même temps qu'un mauvais mot de passe.
        self._dummy = self._hasher.hash(secrets.token_urlsafe(16))
        self._max_concurrent = max_concurrent or max(2, (os.cpu_count() or 2) // 2)
        self._limiter: anyio.CapacityLimiter | None = None

    def _slots(self) -> anyio.CapacityLimiter:
        if self._limiter is None:  # créé dans la boucle d'événements qui s'en sert
            self._limiter = anyio.CapacityLimiter(self._max_concurrent)
        return self._limiter

    async def hash_async(self, password: str) -> str:
        return await anyio.to_thread.run_sync(self.hash, password, limiter=self._slots())

    async def verify_async(self, password_hash: str | None, password: str) -> bool:
        return await anyio.to_thread.run_sync(self.verify, password_hash, password, limiter=self._slots())

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
