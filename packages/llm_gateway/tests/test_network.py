import httpx
import pytest

from evalforge_llm import LLMRequest, OpenAICompatibleProvider, ProviderError, ProviderErrorKind, chat_messages
from evalforge_llm.network import SafeTransport, UnsafeDestinationError, check_url, is_public


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("93.184.215.14", True),
        ("2606:4700::6810:85e5", True),
        ("127.0.0.1", False),
        ("10.1.2.3", False),
        ("172.16.0.1", False),
        ("192.168.1.10", False),
        ("169.254.169.254", False),  # métadonnées cloud
        ("100.64.0.1", False),  # NAT d'opérateur
        ("0.0.0.0", False),
        ("::1", False),
        ("fd00::1", False),
        ("::ffff:127.0.0.1", False),  # IPv4 déguisée en IPv6
        ("224.0.0.1", False),
    ],
)
def test_adresses_publiques_et_interdites(address, public):
    assert is_public(address) is public


@pytest.mark.parametrize(
    "url", ["http://localhost:11434/v1", "http://127.0.0.1/v1", "http://[::1]/v1", "http://169.254.169.254/latest"]
)
def test_une_adresse_interne_visible_est_refusee_des_l_enregistrement(url):
    with pytest.raises(UnsafeDestinationError):
        check_url(url, allow_private=False)
    check_url(url, allow_private=True)


def test_un_nom_d_hote_est_laisse_au_controle_de_connexion():
    check_url("https://api.deepseek.com/v1", allow_private=False)


class Inner(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"ok": True})


def resolving_to(*addresses: str):
    async def resolver(host: str, port: int) -> list[str]:
        return list(addresses)

    return resolver


async def test_la_connexion_part_vers_l_adresse_verifiee_en_gardant_le_nom():
    inner = Inner()
    client = httpx.AsyncClient(transport=SafeTransport(inner, resolver=resolving_to("93.184.215.14")))

    await client.get("https://llm.example/v1/models")

    sent = inner.requests[0]
    assert sent.url.host == "93.184.215.14"
    assert sent.headers["host"] == "llm.example"
    assert sent.extensions["sni_hostname"] == "llm.example"


async def test_un_nom_qui_resout_vers_une_adresse_interne_est_refuse():
    inner = Inner()
    client = httpx.AsyncClient(
        transport=SafeTransport(inner, resolver=resolving_to("93.184.215.14", "169.254.169.254"))
    )

    with pytest.raises(UnsafeDestinationError, match=r"169\.254\.169\.254"):
        await client.get("https://rebind.example/v1")
    assert inner.requests == []


async def test_les_adresses_privees_peuvent_etre_autorisees_pour_un_modele_local():
    inner = Inner()
    client = httpx.AsyncClient(transport=SafeTransport(inner, allow_private=True))

    await client.get("http://localhost:11434/v1/models")

    assert inner.requests[0].url.host == "localhost"


async def test_le_fournisseur_ne_rejoue_pas_une_destination_interdite():
    inner = Inner()
    client = httpx.AsyncClient(transport=SafeTransport(inner, resolver=resolving_to("10.0.0.5")))
    provider = OpenAICompatibleProvider(base_url="https://internal.example/v1", client=client, max_attempts=3)

    with pytest.raises(ProviderError) as raised:
        await provider.generate(LLMRequest(model="m", messages=chat_messages("q")))

    assert raised.value.kind is ProviderErrorKind.FORBIDDEN_DESTINATION
    assert raised.value.attempts == 1
