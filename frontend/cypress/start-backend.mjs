// Démarre le faux fournisseur LLM (8100, aussi destinataire de webhooks), l'agent de support (8200, et 8201 piloté par le modèle négligent), l'API (8000) et un worker pour le test de bout en bout, avec des secrets
// jetables. La base doit exister et être migrée (EVALFORGE_DATABASE_URL), RabbitMQ doit tourner (voir le README).
import { spawn } from 'node:child_process'

const env = {
  ...process.env,
  EVALFORGE_SECRET_KEY: process.env.EVALFORGE_SECRET_KEY ?? 'dGVzdC1jbGUtZmVybmV0LWUyZS0wMTIzNDU2Nzg5YWI=',
  EVALFORGE_JWT_SECRET_KEY: process.env.EVALFORGE_JWT_SECRET_KEY ?? 'cle-e2e-pour-les-jetons-d-acces-0123456789',
  EVALFORGE_COOKIE_SECURE: 'false',
  // Le faux fournisseur écoute sur 127.0.0.1 : adresses locales autorisées pour ce test seulement.
  EVALFORGE_ALLOW_PRIVATE_PROVIDER_URLS: 'true',
  EVALFORGE_ALLOW_PRIVATE_WEBHOOK_URLS: 'true',
  SUPPORT_AGENT_LLM_MODEL: 'support-agent',
}
const options = { env, stdio: 'inherit', shell: true }

// Commandes fixes, sans entrée extérieure : passées entières au shell (uv se résout aussi sous Windows).
const children = [
  spawn('uv run python ../scripts/fake_llm.py 8100', options),
  spawn('uv run evalforge-support-agent', options),
  spawn('uv run evalforge-support-agent', {
    ...options,
    env: { ...env, SUPPORT_AGENT_PORT: '8201', SUPPORT_AGENT_LLM_MODEL: 'careless-agent' },
  }),
  spawn('uv run uvicorn evalforge_api.main:create_app --factory --host 127.0.0.1 --port 8000', options),
  // Les runs sont exécutés par le worker, pas par l'API (RabbitMQ requis : EVALFORGE_RABBITMQ_URL).
  spawn('uv run evalforge-worker', options),
]
const stop = () => children.forEach((child) => child.kill())
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => (stop(), process.exit(0)))
for (const child of children) child.on('exit', (code) => (stop(), process.exit(code ?? 0)))
