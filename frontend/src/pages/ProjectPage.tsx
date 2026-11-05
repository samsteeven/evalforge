import { useQuery } from '@tanstack/react-query'
import { Link, useParams, useSearchParams } from 'react-router'
import { api, unwrap } from '../api/client'
import { Badge, ErrorAlert, Loading, PageTitle } from '../components/ui'
import { DatasetsPanel } from '../features/datasets/DatasetsPanel'
import { ExperimentsPanel } from '../features/experiments/ExperimentsPanel'
import { ModelsPanel } from '../features/models/ModelsPanel'
import { PromptsPanel } from '../features/prompts/PromptsPanel'
import { WebhooksPanel } from '../features/webhooks/WebhooksPanel'

const TABS = [
  { id: 'datasets', label: 'Jeux de données' },
  { id: 'prompts', label: 'Prompts' },
  { id: 'models', label: 'Modèles' },
  { id: 'experiments', label: 'Expériences' },
  { id: 'integrations', label: 'Intégrations' },
] as const

export function ProjectPage() {
  const { projectId = '' } = useParams()
  const [search, setSearch] = useSearchParams()
  const tab = TABS.find((t) => t.id === search.get('tab'))?.id ?? 'datasets'
  const project = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => unwrap(api.GET('/api/v1/projects/{project_id}', { params: { path: { project_id: projectId } } })),
  })

  if (project.isPending) return <Loading />
  if (project.error) return <ErrorAlert error={project.error} />

  return (
    <>
      <p className="mb-2 text-sm text-slate-500">
        <Link to="/" className="hover:text-slate-300">Projets</Link> /
      </p>
      <PageTitle title={project.data.name} actions={<Badge tone="info">{project.data.type.toUpperCase()}</Badge>} />
      <div role="tablist" className="mb-6 flex gap-1 border-b border-slate-800">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setSearch({ tab: t.id })}
            className={
              'border-b-2 px-4 py-2 text-sm font-medium transition ' +
              (tab === t.id ? 'border-indigo-400 text-white' : 'border-transparent text-slate-400 hover:text-slate-200')
            }
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'datasets' && <DatasetsPanel projectId={projectId} />}
      {tab === 'prompts' && <PromptsPanel projectId={projectId} />}
      {tab === 'models' && <ModelsPanel projectId={projectId} />}
      {tab === 'experiments' && <ExperimentsPanel projectId={projectId} />}
      {tab === 'integrations' && <WebhooksPanel projectId={projectId} />}
    </>
  )
}
