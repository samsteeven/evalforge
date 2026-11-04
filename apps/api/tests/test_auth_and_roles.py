"""Comptes, sessions, rôles et protection SSRF à l'enregistrement d'un fournisseur."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from evalforge_api.core.security import AccessTokens
from helpers import PASSWORD, join, new_email, post, register, seed

CSRF = {"X-Requested-With": "EvalForge"}


# --- Comptes et sessions -----------------------------------------------------------------------------------------


async def test_inscription_puis_connexion(client):
    email = new_email()
    registered = await register(client, email.upper())

    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    me = (await client.get("/auth/me", headers=registered["auth"])).json()

    assert login.status_code == 200
    assert login.json()["user"]["email"] == email  # e-mail normalisé en minuscules
    assert me["user"]["email"] == email
    assert [(w["name"], w["role"]) for w in me["workspaces"]] == [("Atelier Nova", "owner")]
    cookie = login.headers["set-cookie"]
    assert "evalforge_refresh=" in cookie
    assert "HttpOnly" in cookie
    assert "Path=/api/v1/auth" in cookie
    assert "SameSite=strict" in cookie


async def test_un_e_mail_deja_utilise_est_refuse(client):
    email = new_email()
    await register(client, email)

    response = await client.post(
        "/auth/register", json={"email": email, "password": PASSWORD, "workspace_name": "Autre"}
    )

    assert response.status_code == 409


@pytest.mark.parametrize("known_email", [True, False])
async def test_un_mauvais_identifiant_donne_la_meme_reponse(client, known_email):
    email = new_email()
    if known_email:
        await register(client, email)

    response = await client.post("/auth/login", json={"email": email, "password": "pas-le-bon-mot-de-passe"})

    assert response.status_code == 401
    assert response.json()["detail"] == "E-mail ou mot de passe incorrect"


async def test_sans_jeton_ou_avec_un_jeton_falsifie_ou_expire_l_acces_est_refuse(client, settings):
    account = await register(client)
    user_id = account["session"]["user"]["id"]
    expired = AccessTokens(settings.jwt_secret_key.get_secret_value(), 15).issue(
        user_id, now=datetime.now(UTC) - timedelta(hours=1)
    )
    forged = jwt.encode({"sub": user_id, "exp": datetime.now(UTC) + timedelta(hours=1), "typ": "access"}, "x" * 32)

    for headers in ({}, {"Authorization": f"Bearer {expired}"}, {"Authorization": f"Bearer {forged}"}):
        response = await client.get("/auth/me", headers=headers)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"


async def test_le_renouvellement_remplace_le_jeton_et_un_rejeu_revoque_la_session(client):
    await register(client)
    first = client.cookies.get("evalforge_refresh")

    renewed = await client.post("/auth/refresh", headers=CSRF)
    second = client.cookies.get("evalforge_refresh")
    assert renewed.status_code == 200
    assert second != first

    # Le jeton déjà utilisé revient (volé) : refusé, et toute la famille tombe avec lui.
    client.cookies.set("evalforge_refresh", first, path="/api/v1/auth")
    replay = await client.post("/auth/refresh", headers=CSRF)
    assert replay.status_code == 401
    assert replay.json()["detail"] == "Session révoquée"

    client.cookies.set("evalforge_refresh", second, path="/api/v1/auth")
    legitimate = await client.post("/auth/refresh", headers=CSRF)
    assert legitimate.status_code == 401


async def test_le_renouvellement_exige_l_en_tete_anti_csrf(client):
    await register(client)

    response = await client.post("/auth/refresh")

    assert response.status_code == 403


async def test_la_deconnexion_revoque_la_session(client):
    await register(client)
    cookie = client.cookies.get("evalforge_refresh")

    assert (await client.post("/auth/logout", headers=CSRF)).status_code == 204
    client.cookies.set("evalforge_refresh", cookie, path="/api/v1/auth")
    assert (await client.post("/auth/refresh", headers=CSRF)).status_code == 401


# --- Rôles -------------------------------------------------------------------------------------------------------


async def test_un_lecteur_lit_mais_ne_modifie_rien(client):
    data = await seed(client, models=("parrot",))
    viewer = await join(client, data["headers"], "viewer")

    assert (await client.get(f"/projects/{data['project']['id']}/experiments", headers=viewer)).status_code == 200
    attempts = [
        ("/projects", {"name": "Autre"}),
        (f"/projects/{data['project']['id']}/datasets", {"name": "Autre"}),
        (f"/experiments/{data['experiment']['id']}/runs", {"dataset_version_id": data["version"]["id"]}),
    ]
    for url, body in attempts:
        response = await client.post(url, json=body, headers=viewer)
        assert response.status_code == 403, url


async def test_seul_un_administrateur_configure_un_modele_et_sa_cle(client):
    data = await seed(client, models=("parrot",))
    member = await join(client, data["headers"], "member")
    body = {
        "name": "deepseek",
        "provider_kind": "openai_compatible",
        "base_url": "https://api.deepseek.com/v1",
        "model_name": "deepseek-chat",
        "api_key": "sk-x",
    }

    response = await client.post(f"/projects/{data['project']['id']}/providers", json=body, headers=member)
    created = await client.post(f"/projects/{data['project']['id']}/providers", json=body, headers=data["headers"])

    assert response.status_code == 403
    assert created.status_code == 201


async def test_un_administrateur_ne_peut_pas_nommer_un_proprietaire(client):
    owner = await create_owner(client)
    admin = await join(client, owner, "admin")
    other = await register(client)

    response = await client.post(
        "/workspaces/current/members", json={"email": other["email"], "role": "owner"}, headers=admin
    )

    assert response.status_code == 403


async def test_un_membre_retire_perd_l_acces_immediatement(client):
    owner = await create_owner(client)
    member = await join(client, owner, "member")
    members = (await client.get("/workspaces/current/members", headers=owner)).json()
    member_id = next(m["user_id"] for m in members if m["role"] == "member")

    assert (await client.delete(f"/workspaces/current/members/{member_id}", headers=owner)).status_code == 204
    # Même jeton d'accès, toujours valide : l'appartenance est relue à chaque requête.
    assert (await client.get("/projects", headers=member)).status_code == 404


async def test_le_dernier_proprietaire_ne_peut_pas_etre_retire(client):
    account = await register(client)
    owner_id = account["session"]["user"]["id"]

    response = await client.delete(f"/workspaces/current/members/{owner_id}", headers=account["headers"])

    assert response.status_code == 409


async def create_owner(client) -> dict:
    return (await register(client))["headers"]


# --- Protection SSRF ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    ["http://localhost:11434/v1", "http://127.0.0.1:8000/v1", "http://169.254.169.254/latest", "http://[::1]/v1"],
)
async def test_une_adresse_interne_de_fournisseur_est_refusee(client, url):
    data = await seed(client, models=("parrot",))

    response = await client.post(
        f"/projects/{data['project']['id']}/providers",
        json={"name": "interne", "provider_kind": "ollama", "base_url": url, "model_name": "llama3"},
        headers=data["headers"],
    )

    assert response.status_code == 422
    assert "refusée" in response.json()["detail"]


async def test_les_adresses_locales_sont_acceptees_si_la_configuration_l_autorise(app_factory, settings):
    async with app_factory(settings.model_copy(update={"allow_private_provider_urls": True})) as client:
        data = await seed(client, models=("parrot",))
        created = await post(
            client,
            f"/projects/{data['project']['id']}/providers",
            data["headers"],
            {"name": "ollama", "provider_kind": "ollama", "base_url": "http://localhost:11434/v1", "model_name": "q"},
        )

    assert created["base_url"] == "http://localhost:11434/v1"
