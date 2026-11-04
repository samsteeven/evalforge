import createClient, { type Middleware } from 'openapi-fetch'
import type { components, paths } from './schema'

export type Schemas = components['schemas']

// Le jeton d'accès ne vit qu'en mémoire : ni localStorage ni cookie lisible par le JavaScript.
let accessToken: string | null = null
let workspaceId: string | null = null
let onUnauthorized: (() => void) | null = null

export function setAccessToken(token: string | null): void {
  accessToken = token
}

export function setWorkspaceId(id: string | null): void {
  workspaceId = id
}

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler
}

const session: Middleware = {
  onRequest({ request }) {
    if (accessToken) request.headers.set('Authorization', `Bearer ${accessToken}`)
    if (workspaceId) request.headers.set('X-Workspace-Id', workspaceId)
    // Exigé par les routes qui lisent le cookie de session : un autre site ne peut pas l'ajouter.
    request.headers.set('X-Requested-With', 'EvalForge')
    return request
  },
  onResponse({ request, response }) {
    if (response.status === 401 && !new URL(request.url).pathname.startsWith('/api/v1/auth/')) {
      onUnauthorized?.()
    }
    return response
  },
}

export const api = createClient<paths>({ baseUrl: '', credentials: 'same-origin' })
api.use(session)

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

type ValidationItem = { loc?: (string | number)[]; msg?: string }

export function describeError(error: unknown, status: number): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return (detail as ValidationItem[])
      .map((item) => {
        const field = item.loc?.filter((part) => part !== 'body').join('.')
        return field ? `${field} : ${item.msg}` : (item.msg ?? '')
      })
      .join(' · ')
  }
  return `Erreur ${status}`
}

type FetchResult<T> = { data?: T; error?: unknown; response: Response }

/** Renvoie les données, ou lève une ApiError lisible. */
export async function unwrap<T>(request: Promise<FetchResult<T>>): Promise<T> {
  const { data, error, response } = await request
  if (!response.ok) throw new ApiError(response.status, describeError(error, response.status))
  return data as T
}

/** Comme unwrap, avec le code HTTP : 200 et 201 n'ont pas le même sens pour une version de jeu ou de prompt. */
export async function unwrapWithStatus<T>(request: Promise<FetchResult<T>>): Promise<{ data: T; status: number }> {
  const { data, error, response } = await request
  if (!response.ok) throw new ApiError(response.status, describeError(error, response.status))
  return { data: data as T, status: response.status }
}
