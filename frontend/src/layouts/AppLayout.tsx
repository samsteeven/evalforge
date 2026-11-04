import { Link, Navigate, Outlet } from 'react-router'
import { useAuth } from '../auth/useAuth'
import { Badge, Button, Select } from '../components/ui'
import { ROLE_LABEL } from '../lib/format'

export function AppLayout() {
  const { state, logout, selectWorkspace } = useAuth()
  if (state.status === 'loading') return <p className="p-8 text-sm text-slate-500">Ouverture de la session…</p>
  if (state.status === 'anonymous') return <Navigate to="/login" replace />

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-800 bg-slate-950/80 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-4 px-6 py-3">
          <Link to="/" className="text-lg font-semibold tracking-tight">
            Eval<span className="text-indigo-400">Forge</span>
          </Link>
          <nav className="text-sm text-slate-300">
            <Link to="/" className="rounded-md px-2 py-1 hover:bg-slate-800">Projets</Link>
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <Select
              aria-label="Espace de travail"
              className="w-48"
              value={state.workspace.workspace_id}
              onChange={(event) => selectWorkspace(event.target.value)}
            >
              {state.workspaces.map((w) => (
                <option key={w.workspace_id} value={w.workspace_id}>{w.name}</option>
              ))}
            </Select>
            <Badge>{ROLE_LABEL[state.workspace.role] ?? state.workspace.role}</Badge>
            <span className="hidden max-w-56 truncate text-sm text-slate-400 lg:inline" title={state.user.email}>
              {state.user.email}
            </span>
            <Button variant="ghost" onClick={() => void logout()}>Se déconnecter</Button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-6 py-8">
        <Outlet />
      </main>
    </div>
  )
}
