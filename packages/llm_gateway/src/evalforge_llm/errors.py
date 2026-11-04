from enum import StrEnum


class ProviderErrorKind(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTHENTICATION = "authentication"
    BAD_REQUEST = "bad_request"
    SERVER = "server"
    NETWORK = "network"
    INVALID_RESPONSE = "invalid_response"


_RETRYABLE = {
    ProviderErrorKind.TIMEOUT,
    ProviderErrorKind.RATE_LIMITED,
    ProviderErrorKind.SERVER,
    ProviderErrorKind.NETWORK,
}


class ProviderError(Exception):
    """Erreur normalisée d'un fournisseur. Le message ne contient jamais la clé d'API."""

    def __init__(
        self,
        kind: ProviderErrorKind,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.retry_after = retry_after
        self.attempts = 1

    @property
    def retryable(self) -> bool:
        return self.kind in _RETRYABLE

    def __str__(self) -> str:
        suffix = f" (après {self.attempts} tentatives)" if self.attempts > 1 else ""
        return f"{self.kind.value} : {self.args[0]}{suffix}"
