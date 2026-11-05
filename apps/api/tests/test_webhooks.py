"""Webhooks : abonnement, fin de run publiée par l'outbox, envoi signé par le worker, reprises et refus définitifs."""

import asyncio
import json

from evalforge_api.modules.webhooks.delivery import verify
from helpers import QUESTIONS, join, post, seed, start_run, wait_for_run


async def subscribe(client, data, url: str, **extra) -> dict:
    return await post(client, f"/projects/{data['project']['id']}/webhooks", data["headers"], {"url": url, **extra})


async def wait_for_deliveries(client, headers, webhook_id: str, count: int = 1, timeout: float = 15) -> list[dict]:
    """Attend que les livraisons ne soient plus en attente (livrées ou en échec)."""
    async with asyncio.timeout(timeout):
        while True:
            deliveries = (await client.get(f"/webhooks/{webhook_id}/deliveries", headers=headers)).json()
            if len(deliveries) >= count and all(d["status"] != "pending" for d in deliveries):
                return deliveries
            await asyncio.sleep(0.05)


async def test_une_fin_de_run_est_envoyee_signee_avec_le_resume(client, fake_llm, webhook_receiver):
    fake_llm.answers = dict(QUESTIONS)
    data = await seed(client)
    hook = await subscribe(client, data, "https://ok.test/evalforge")
    assert hook["secret"].startswith("whsec_")
    assert hook["events"] == ["evaluation.completed", "evaluation.failed"]

    run = await wait_for_run(client, data["headers"], await start_run(client, data))
    (delivery,) = await wait_for_deliveries(client, data["headers"], hook["id"])

    assert delivery["status"] == "delivered"
    assert (delivery["attempts"], delivery["response_status"], delivery["run_id"]) == (1, 204, run["id"])
    (request,) = webhook_receiver.received("ok.test")
    assert request.headers["X-EvalForge-Event"] == "evaluation.completed"
    assert request.headers["X-EvalForge-Delivery"] == delivery["id"]
    assert verify(
        hook["secret"],
        request.headers["X-EvalForge-Timestamp"],
        request.content,
        request.headers["X-EvalForge-Signature"],
    )
    assert not verify(
        "whsec_autre",
        request.headers["X-EvalForge-Timestamp"],
        request.content,
        request.headers["X-EvalForge-Signature"],
    )
    payload = json.loads(request.content)
    assert payload["run"]["id"] == run["id"]
    assert payload["run"]["status"] == "completed"
    rates = {m["model"]: m["pass_rates"] for m in payload["models"]}
    assert rates == {
        "parrot": {"exact_match": 1.0, "contains_expected": 1.0},
        "vague": {"exact_match": 0.0, "contains_expected": 0.0},
    }
    assert payload["regression"] is None  # pas de baseline


async def test_le_secret_n_est_montre_qu_a_la_creation_et_seul_un_admin_gere_les_webhooks(client):
    data = await seed(client)
    await subscribe(client, data, "https://ok.test/evalforge", events=["evaluation.failed"])
    member = await join(client, data["headers"], "member")
    url = f"/projects/{data['project']['id']}/webhooks"

    listed = (await client.get(url, headers=data["headers"])).json()
    refused = await client.post(url, json={"url": "https://ok.test/x"}, headers=member)
    hidden = await client.get(url, headers=member)

    assert [h["events"] for h in listed] == [["evaluation.failed"]]
    assert "secret" not in listed[0]
    assert refused.status_code == 403
    assert hidden.status_code == 403


async def test_une_adresse_interne_ou_un_evenement_inconnu_est_refuse(client):
    data = await seed(client)
    url = f"/projects/{data['project']['id']}/webhooks"

    internal = await client.post(url, json={"url": "http://169.254.169.254/latest"}, headers=data["headers"])
    unknown = await client.post(
        url, json={"url": "https://ok.test/x", "events": ["evaluation.requested"]}, headers=data["headers"]
    )

    assert internal.status_code == 422
    assert "Adresse de webhook refusée" in internal.text
    assert unknown.status_code == 422


async def test_reprise_sur_erreur_temporaire_et_abandon_sur_refus_definitif(client, fake_llm, webhook_receiver):
    fake_llm.answers = dict(QUESTIONS)
    data = await seed(client, models=("parrot",))
    flaky = await subscribe(client, data, "https://flaky.test/hook")
    gone = await subscribe(client, data, "https://gone.test/hook")
    down = await subscribe(client, data, "https://down.test/hook")

    await wait_for_run(client, data["headers"], await start_run(client, data))
    (recovered,) = await wait_for_deliveries(client, data["headers"], flaky["id"])
    (refused,) = await wait_for_deliveries(client, data["headers"], gone["id"])
    (abandoned,) = await wait_for_deliveries(client, data["headers"], down["id"])

    assert (recovered["status"], recovered["attempts"], recovered["response_status"]) == ("delivered", 2, 200)
    assert (refused["status"], refused["attempts"], refused["last_error"]) == ("failed", 1, "HTTP 410")
    assert (abandoned["status"], abandoned["attempts"]) == ("failed", 3)
    assert abandoned["last_error"] == "HTTP 503 (tentatives épuisées)"
    # Une livraison réussie ou refusée n'est jamais renvoyée lors des reprises des autres.
    assert len(webhook_receiver.received("flaky.test")) == 2
    assert len(webhook_receiver.received("gone.test")) == 1
    assert len(webhook_receiver.received("down.test")) == 3


async def test_la_charge_utile_signale_une_regression_face_a_la_baseline(client, fake_llm, webhook_receiver):
    fake_llm.answers = dict(QUESTIONS)
    data = await seed(client, models=("parrot",))
    baseline = await wait_for_run(client, data["headers"], await start_run(client, data))
    await client.put(
        f"/projects/{data['project']['id']}/baseline",
        json={"run_id": baseline["id"], "tolerance": 0.0},
        headers=data["headers"],
    )
    hook = await subscribe(client, data, "https://ok.test/ci", events=["evaluation.completed"])
    fake_llm.answers = {}  # le modèle ne trouve plus rien : tout casse

    candidate = await wait_for_run(client, data["headers"], await start_run(client, data))
    # La fin du run de baseline peut arriver après l'abonnement : on retient la livraison du run candidat.
    async with asyncio.timeout(15):
        while not [
            d for d in await wait_for_deliveries(client, data["headers"], hook["id"]) if d["run_id"] == candidate["id"]
        ]:
            await asyncio.sleep(0.05)

    (request,) = [
        r for r in webhook_receiver.received("ok.test") if json.loads(r.content)["run"]["id"] == candidate["id"]
    ]
    regression = json.loads(request.content)["regression"]
    assert regression["baseline_run_id"] == baseline["id"]
    assert regression["comparable"] is True
    assert regression["regressed"] is True
    assert {(r["model"], r["metric"], r["baseline_rate"], r["candidate_rate"]) for r in regression["regressions"]} == {
        ("parrot", "exact_match", 1.0, 0.0),
        ("parrot", "contains_expected", 1.0, 0.0),
    }


async def test_un_webhook_supprime_ne_recoit_plus_rien(client, fake_llm, webhook_receiver):
    fake_llm.answers = dict(QUESTIONS)
    data = await seed(client, models=("parrot",))
    hook = await subscribe(client, data, "https://ok.test/x")

    deleted = await client.delete(f"/webhooks/{hook['id']}", headers=data["headers"])
    await wait_for_run(client, data["headers"], await start_run(client, data))
    await asyncio.sleep(0.5)

    assert deleted.status_code == 204
    assert webhook_receiver.received("ok.test") == []
    assert (await client.get(f"/webhooks/{hook['id']}/deliveries", headers=data["headers"])).status_code == 404
