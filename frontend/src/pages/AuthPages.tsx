import { useState, type FormEvent, type ReactNode } from 'react'
import { Link, Navigate } from 'react-router'
import { useAuth } from '../auth/useAuth'
import { Button, ErrorAlert, Field, Input } from '../components/ui'

function AuthFrame({ title, children, footer }: { title: string; children: ReactNode; footer: ReactNode }) {
  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <p className="mb-8 text-center text-xl font-semibold tracking-tight">
          Eval<span className="text-indigo-400">Forge</span>
        </p>
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6 shadow-2xl shadow-black/40">
          <h1 className="mb-5 text-lg font-semibold">{title}</h1>
          {children}
        </div>
        <p className="mt-6 text-center text-sm text-slate-400">{footer}</p>
      </div>
    </main>
  )
}

function useSubmit(action: () => Promise<void>) {
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await action()
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }
  return { error, busy, submit }
}

export function LoginPage() {
  const { state, login } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const { error, busy, submit } = useSubmit(() => login(email, password))
  if (state.status === 'authenticated') return <Navigate to="/" replace />

  return (
    <AuthFrame title="Connexion" footer={<>Pas encore de compte ? <Link className="text-indigo-300 hover:underline" to="/register">Créer un compte</Link></>}>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Adresse e-mail">
          <Input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Mot de passe">
          <Input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <ErrorAlert error={error} />
        <Button type="submit" busy={busy} className="w-full">Se connecter</Button>
      </form>
    </AuthFrame>
  )
}

export function RegisterPage() {
  const { state, register } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [workspaceName, setWorkspaceName] = useState('')
  const { error, busy, submit } = useSubmit(() => register(email, password, workspaceName))
  if (state.status === 'authenticated') return <Navigate to="/" replace />

  return (
    <AuthFrame title="Créer un compte" footer={<>Déjà inscrit ? <Link className="text-indigo-300 hover:underline" to="/login">Se connecter</Link></>}>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Adresse e-mail">
          <Input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Mot de passe" hint="10 caractères au minimum">
          <Input type="password" autoComplete="new-password" minLength={10} required value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Field label="Nom de l'espace de travail" hint="Votre équipe ou votre organisation">
          <Input required value={workspaceName} onChange={(e) => setWorkspaceName(e.target.value)} />
        </Field>
        <ErrorAlert error={error} />
        <Button type="submit" busy={busy} className="w-full">Créer le compte</Button>
      </form>
    </AuthFrame>
  )
}
