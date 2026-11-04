import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, setAccessToken, setUnauthorizedHandler, setWorkspaceId, unwrap, type Schemas } from '../api/client'
import { AuthContext, type AuthContextValue, type AuthState } from './useAuth'

const WORKSPACE_KEY = 'evalforge.workspace'

function rememberedWorkspace(): string | null {
  try {
    return localStorage.getItem(WORKSPACE_KEY)
  } catch {
    return null
  }
}

function rememberWorkspace(id: string): void {
  try {
    localStorage.setItem(WORKSPACE_KEY, id)
  } catch {
    // stockage indisponible (navigation privée) : le premier espace sera repris au prochain chargement
  }
}

// Un seul renouvellement à la fois : deux appels simultanés présenteraient le même jeton, et le second
// serait pris pour un rejeu, ce qui révoque la session.
let refreshing: Promise<Schemas['SessionOut'] | null> | null = null

function refreshSession(): Promise<Schemas['SessionOut'] | null> {
  refreshing ??= api
    .POST('/api/v1/auth/refresh')
    .then(({ data, response }) => (response.ok && data ? data : null))
    .catch(() => null)
    .finally(() => {
      refreshing = null
    })
  return refreshing
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: 'loading' })
  const queryClient = useQueryClient()
  const timer = useRef<number | undefined>(undefined)
  const renew = useRef<() => void>(() => {})

  const signOut = useCallback(() => {
    window.clearTimeout(timer.current)
    setAccessToken(null)
    setWorkspaceId(null)
    queryClient.clear()
    setState({ status: 'anonymous' })
  }, [queryClient])

  const open = useCallback(async (session: Schemas['SessionOut']) => {
    setAccessToken(session.access_token)
    // Renouvelé une minute avant l'expiration du jeton d'accès.
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => renew.current(), Math.max(session.expires_in - 60, 30) * 1000)
    const me = await unwrap(api.GET('/api/v1/auth/me'))
    const remembered = rememberedWorkspace()
    const workspace = me.workspaces.find((w) => w.workspace_id === remembered) ?? me.workspaces[0]
    if (!workspace) throw new Error('Aucun espace de travail')
    setWorkspaceId(workspace.workspace_id)
    setState({ status: 'authenticated', user: me.user, workspaces: me.workspaces, workspace })
  }, [])

  useEffect(() => {
    renew.current = () => {
      void refreshSession().then((session) => (session ? open(session) : signOut()))
    }
    // Au chargement, le cookie de session (s'il existe) rouvre la session sans redemander le mot de passe.
    renew.current()
    setUnauthorizedHandler(() => {
      void refreshSession().then((session) =>
        session ? open(session).then(() => queryClient.invalidateQueries()) : signOut(),
      )
    })
    return () => {
      setUnauthorizedHandler(null)
      window.clearTimeout(timer.current)
    }
  }, [open, signOut, queryClient])

  const value = useMemo<AuthContextValue>(
    () => ({
      state,
      login: async (email, password) => {
        await open(await unwrap(api.POST('/api/v1/auth/login', { body: { email, password } })))
      },
      register: async (email, password, workspaceName) => {
        const body = { email, password, workspace_name: workspaceName }
        await open(await unwrap(api.POST('/api/v1/auth/register', { body })))
      },
      logout: async () => {
        await api.POST('/api/v1/auth/logout')
        signOut()
      },
      selectWorkspace: (workspaceId) => {
        if (state.status !== 'authenticated') return
        const workspace = state.workspaces.find((w) => w.workspace_id === workspaceId)
        if (!workspace) return
        rememberWorkspace(workspace.workspace_id)
        setWorkspaceId(workspace.workspace_id)
        queryClient.clear()
        setState({ ...state, workspace })
      },
    }),
    [state, open, signOut, queryClient],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
