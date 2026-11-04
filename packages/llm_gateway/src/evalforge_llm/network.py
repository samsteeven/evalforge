"""Protection SSRF : l'adresse d'un fournisseur est saisie par l'utilisateur, et c'est le serveur qui l'appelle.

Le contrôle qui fait foi porte sur l'adresse réellement contactée : le nom est résolu, chaque adresse est
vérifiée, puis la connexion part vers l'adresse vérifiée (le nom reste dans l'en-tête Host et dans le SNI TLS,
le certificat est donc toujours contrôlé). Un DNS qui répond une adresse publique puis une adresse interne
(DNS rebinding) ne peut pas glisser entre la vérification et la connexion.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable

import httpx

Resolver = Callable[[str, int], Awaitable[list[str]]]

_LOCAL_NAMES = ("localhost", "localhost.localdomain")


class UnsafeDestinationError(Exception):
    """Destination interdite : adresse privée, de bouclage, lien local, métadonnées cloud…"""


def is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_url(url: str, *, allow_private: bool) -> None:
    """Refus immédiat, à l'enregistrement, de ce qui se voit sans résolution DNS."""
    if allow_private:
        return
    host = (httpx.URL(url).host or "").lower().rstrip(".")
    if host in _LOCAL_NAMES or host.endswith(".localhost"):
        raise UnsafeDestinationError(f"adresse locale refusée : {host}")
    try:
        if not is_public(host):
            raise UnsafeDestinationError(f"adresse non publique refusée : {host}")
    except ValueError:
        pass  # un nom : vérifié à chaque connexion par SafeTransport


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


class SafeTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        inner: httpx.AsyncBaseTransport | None = None,
        *,
        allow_private: bool = False,
        resolver: Resolver = system_resolver,
    ) -> None:
        self._inner = inner or httpx.AsyncHTTPTransport()
        self._allow_private = allow_private
        self._resolver = resolver

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._allow_private:
            return await self._inner.handle_async_request(request)

        host = request.url.host
        port = request.url.port or (443 if request.url.scheme == "https" else 80)
        try:
            ipaddress.ip_address(host)
            addresses = [host]
        except ValueError:
            try:
                addresses = await self._resolver(host, port)
            except OSError:
                raise httpx.ConnectError(f"hôte introuvable : {host}", request=request) from None
        if not addresses:
            raise httpx.ConnectError(f"hôte introuvable : {host}", request=request)
        unsafe = [address for address in addresses if not is_public(address)]
        if unsafe:
            raise UnsafeDestinationError(f"{host} pointe vers une adresse non publique ({unsafe[0]})")

        # Connexion vers l'adresse vérifiée ; l'en-tête Host d'origine est conservé par httpx.
        request.url = request.url.copy_with(host=addresses[0])
        if request.url.scheme == "https":
            request.extensions = {**request.extensions, "sni_hostname": host}
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()
