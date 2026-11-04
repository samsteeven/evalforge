import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { api, unwrap } from '../api/client'
import { useWorkspace } from '../auth/useAuth'
import { Badge, Button, Card, Empty, ErrorAlert, Field, Input, Loading, PageTitle, Select } from '../components/ui'
import { when } from '../lib/format'

const TYPE_LABEL: Record<string, string> = { llm: 'LLM', rag: 'RAG', agent: 'Agent' }

export function ProjectsPage() {
  const { workspace, canEdit } = useWorkspace()
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => unwrap(api.GET('/api/v1/projects')) })

  return (
    <>
      <PageTitle title="Projets" subtitle={`Applications IA évaluées dans l'espace « ${workspace.name} »`} />
      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div>
          {projects.isPending && <Loading />}
          <ErrorAlert error={projects.error} />
          {projects.data?.length === 0 && <Empty>Aucun projet pour l'instant.</Empty>}
          <ul className="grid gap-3 sm:grid-cols-2">
            {projects.data?.map((project) => (
              <li key={project.id}>
                <Link
                  to={`/projects/${project.id}`}
                  className="block rounded-xl border border-slate-800 bg-slate-900/60 p-5 transition hover:border-indigo-500/60 hover:bg-slate-900"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-white">{project.name}</span>
                    <Badge tone="info">{TYPE_LABEL[project.type] ?? project.type}</Badge>
                  </div>
                  <p className="mt-2 text-xs text-slate-500">Créé le {when(project.created_at)}</p>
                </Link>
              </li>
            ))}
          </ul>
        </div>
        {canEdit && <CreateProject />}
      </div>
    </>
  )
}

function CreateProject() {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [type, setType] = useState<'llm' | 'rag' | 'agent'>('rag')
  const create = useMutation({
    mutationFn: () => unwrap(api.POST('/api/v1/projects', { body: { name, type } })),
    onSuccess: () => {
      setName('')
      void queryClient.invalidateQueries({ queryKey: ['projects'] })
    },
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    create.mutate()
  }

  return (
    <Card className="h-fit">
      <h2 className="mb-4 font-medium">Nouveau projet</h2>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Nom du projet">
          <Input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Customer Support AI" />
        </Field>
        <Field label="Type d'application">
          <Select value={type} onChange={(e) => setType(e.target.value as typeof type)}>
            <option value="rag">RAG</option>
            <option value="llm">LLM</option>
            <option value="agent">Agent</option>
          </Select>
        </Field>
        <ErrorAlert error={create.error} />
        <Button type="submit" busy={create.isPending} className="w-full">Créer le projet</Button>
      </form>
    </Card>
  )
}
