"""Argon2 hors de la boucle d'événements, et en nombre borné : constat du test de charge (ADR-026)."""

import asyncio
import threading
import time

from evalforge_api.core.security import Passwords


async def test_le_hachage_ne_gele_pas_la_boucle_et_reste_borne():
    passwords = Passwords(max_concurrent=2)
    lock, running, peak = threading.Lock(), [0], [0]
    real_hash = passwords.hash

    def slow_hash(password: str) -> str:
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        try:
            time.sleep(0.1)  # comme un calcul Argon2 coûteux, hors de la boucle
            return real_hash(password)
        finally:
            with lock:
                running[0] -= 1

    passwords.hash = slow_hash
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    task = asyncio.create_task(ticker())
    hashes = await asyncio.gather(*(passwords.hash_async(f"mot-de-passe-{i}") for i in range(6)))
    task.cancel()

    assert peak[0] == 2  # six demandes, jamais plus de deux calculs à la fois
    assert ticks >= 20  # la boucle a continué de tourner pendant les ~0,3 s de calcul
    assert await passwords.verify_async(hashes[3], "mot-de-passe-3")
    assert not await passwords.verify_async(hashes[3], "autre")
    assert not await passwords.verify_async(None, "mot-de-passe-3")
