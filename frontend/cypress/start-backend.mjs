// Démarre le faux fournisseur LLM (8100) et l'API (8000) pour le test de bout en bout, avec des secrets jetables.
// La base doit exister et être migrée (EVALFORGE_DATABASE_URL, voir le README).
import { spawn } from 'node:child_process'

const env = {
  ...process.env,
  EVALFORGE_SECRET_KEY: process.env.EVALFORGE_SECRET_KEY ?? 'dGVzdC1jbGUtZmVybmV0LWUyZS0wMTIzNDU2Nzg5YWI=',
  EVALFORGE_JWT_SECRET_KEY: process.env.EVALFORGE_JWT_SECRET_KEY ?? 'cle-e2e-pour-les-jetons-d-acces-0123456789',
  EVALFORGE_COOKIE_SECURE: 'false',
  // Le faux fournisseur écoute sur 127.0.0.1 : adresses locales autorisées pour ce test seulement.
  EVALFORGE_ALLOW_PRIVATE_PROVIDER_URLS: 'true',
}
const options = { env, stdio: 'inherit', shell: true }

// Commandes fixes, sans entrée extérieure : passées entières au shell (uv se résout aussi sous Windows).
const children = [
  spawn('uv run python ../scripts/fake_llm.py 8100', options),
  spawn('uv run uvicorn evalforge_api.main:create_app --factory --host 127.0.0.1 --port 8000', options),
]
const stop = () => children.forEach((child) => child.kill())
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => (stop(), process.exit(0)))
for (const child of children) child.on('exit', (code) => (stop(), process.exit(code ?? 0)))
