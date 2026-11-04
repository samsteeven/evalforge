import { createContext, useContext } from 'react'
import type { Schemas } from '../api/client'

export type Membership = Schemas['MembershipOut']

export type AuthState =
  | { status: 'loading' }
  | { status: 'anonymous' }
  | { status: 'authenticated'; user: Schemas['UserOut']; workspaces: Membership[]; workspace: Membership }

export type AuthContextValue = {
  state: AuthState
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, workspaceName: string) => Promise<void>
  logout: () => Promise<void>
  selectWorkspace: (workspaceId: string) => void
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth hors de AuthProvider')
  return context
}

/** L'espace courant et les droits de l'utilisateur : seulement sous une route authentifiée. */
export function useWorkspace() {
  const { state } = useAuth()
  if (state.status !== 'authenticated') throw new Error('useWorkspace sans session')
  const role = state.workspace.role
  return {
    workspace: state.workspace,
    canEdit: role !== 'viewer',
    canAdmin: role === 'admin' || role === 'owner',
  }
}
