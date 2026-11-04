import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router'
import { api, unwrap, type Schemas } from '../api/client'
import { Badge, Card, ErrorAlert, Loading, PageTitle } from '../components/ui'
import { ModelComparison } from '../features/runs/ModelComparison'
import { milliseconds, RESULT_STATUS, RUN_STATUS, usd, when } from '../lib/format'

const ACTIVE = new Set(['queued', 'running'])

type Configuration = {
  prompt: { name: string; version: number; template: string }
  dataset_version: { version: number; checksum: string }
  models: { name: string; model_name: string }[]
  metrics: string[]
}

export function RunPage() {
  const { runId = '' } = useParams()
  const path = { params: { path: { run_id: runId } } }
  const run = useQuery({
    queryKey: ['run', runId],
    queryFn: () => unwrap(api.GET('/api/v1/runs/{run_id}', path)),
    // Rafraîchi chaque seconde tant que le run tourne.
    refetchInterval: (query) => (query.state.data && !ACTIVE.has(query.state.data.status) ? false : 1000),
  })
  const active = !run.data || ACTIVE.has(run.data.status)
  const progress = run.data?.completed_items
  const metrics = useQuery({
    queryKey: ['run-metrics', runId, progress, run.data?.status],
    queryFn: () => unwrap(api.GET('/api/v1/runs/{run_id}/metrics', path)),
    enabled: !!run.data,
    placeholderData: (previous) => previous,
  })
  const results = useQuery({
    queryKey: ['run-results', runId, progress, run.data?.status],
    queryFn: () => unwrap(api.GET('/api/v1/runs/{run_id}/results', { params: { path: { run_id: runId }, query: { limit: 500 } } })),
    enabled: !!run.data,
    placeholderData: (previous) => previous,
  })
  const experiment = useQuery({
    queryKey: ['experiment', run.data?.experiment_id],
    queryFn: () =>
      unwrap(api.GET('/api/v1/experiments/{experiment_id}', { params: { path: { experiment_id: run.data!.experiment_id } } })),
    enabled: !!run.data,
  })

  if (run.isPending) return <Loading />
  if (run.error) return <ErrorAlert error={run.error} />
  const data = run.data
  const config = data.configuration as unknown as Configuration
  const status = RUN_STATUS[data.status] ?? { label: data.status, tone: 'neutral' as const }
  const ratio = data.total_items ? data.completed_items / data.total_items : 0

  return (
    <>
      <p className="mb-2 text-sm text-slate-500">
        {experiment.data && (
          <Link to={`/projects/${experiment.data.project_id}?tab=experiments`} className="hover:text-slate-300">
            ← {experiment.data.name}
          </Link>
        )}
      </p>
      <PageTitle
        title="Run d'évaluation"
        subtitle={`Lancé le ${when(data.created_at)}${data.finished_at ? ` · terminé le ${when(data.finished_at)}` : ''}`}
        actions={<Badge tone={status.tone}>{status.label}</Badge>}
      />

      <Card className="mb-6">
        <div className="mb-2 flex justify-between text-sm">
          <span className="text-slate-300">{data.completed_items} / {data.total_items} réponses</span>
          {active && <span className="text-sky-300">Exécution en cours…</span>}
        </div>
        <div className="h-2 rounded-full bg-slate-800" role="progressbar" aria-valuenow={Math.round(ratio * 100)} aria-valuemin={0} aria-valuemax={100}>
          <div className="h-2 rounded-full bg-indigo-400 transition-all" style={{ width: `${ratio * 100}%` }} />
        </div>
        {data.error && <p className="mt-3 text-sm text-amber-300">{data.error}</p>}
        <dl className="mt-4 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
          <dt className="text-slate-500">Prompt</dt>
          <dd>{config.prompt.name} v{config.prompt.version}</dd>
          <dt className="text-slate-500">Jeu de données</dt>
          <dd>
            v{config.dataset_version.version} <code className="text-xs text-slate-500">sha256 {config.dataset_version.checksum.slice(0, 12)}</code>
          </dd>
          <dt className="text-slate-500">Modèles</dt>
          <dd>{config.models.map((m) => `${m.name} (${m.model_name})`).join(', ')}</dd>
          <dt className="text-slate-500">Exécution</dt>
          <dd>
            {String(data.execution.max_concurrency)} en parallèle, délai {String(data.execution.timeout_seconds)} s par réponse
          </dd>
        </dl>
      </Card>

      <ErrorAlert error={metrics.error} />
      {metrics.data && <ModelComparison models={metrics.data.models} />}

      <Card className="mt-6 overflow-x-auto">
        <h2 className="mb-4 font-medium">Réponses</h2>
        <ErrorAlert error={results.error} />
        <ResultsTable results={results.data ?? []} />
      </Card>
    </>
  )
}

function ResultsTable({ results }: { results: Schemas['ResultOut'][] }) {
  if (results.length === 0) return <p className="text-sm text-slate-500">Pas encore de réponse.</p>
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-xs text-slate-500">
        <tr>
          <th className="pb-2 font-medium">Cas</th>
          <th className="pb-2 font-medium">Modèle</th>
          <th className="pb-2 font-medium">Réponse</th>
          <th className="pb-2 font-medium">Métriques</th>
          <th className="pb-2 text-right font-medium">Latence</th>
          <th className="pb-2 text-right font-medium">Coût</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-slate-800 align-top">
        {results.map((result) => {
          const status = RESULT_STATUS[result.status] ?? { label: result.status, tone: 'warning' as const }
          return (
            <tr key={result.id} data-testid="result-row">
              <td className="py-2 pr-3 font-mono text-xs text-slate-400">{result.case_id}</td>
              <td className="py-2 pr-3 whitespace-nowrap">{result.model}</td>
              <td className="max-w-md py-2 pr-3">
                {result.status === 'success' ? (
                  <span className="text-slate-200">{result.actual_output}</span>
                ) : (
                  <span className="space-x-2">
                    <Badge tone={status.tone}>{status.label}</Badge>
                    <span className="text-rose-200">{result.error}</span>
                  </span>
                )}
              </td>
              <td className="py-2 pr-3">
                <div className="flex flex-wrap gap-1">
                  {result.metrics.map((metric) => (
                    <Badge key={metric.name} tone={metric.passed == null ? 'neutral' : metric.passed ? 'success' : 'danger'}>
                      <span title={metric.reason ?? undefined}>
                        {metric.passed == null ? '—' : metric.passed ? '✓' : '✗'} {metric.name}
                      </span>
                    </Badge>
                  ))}
                </div>
              </td>
              <td className="py-2 text-right tabular-nums">{milliseconds(result.latency_ms)}</td>
              <td className="py-2 text-right tabular-nums text-slate-400">{result.status === 'success' ? usd(result.cost_usd) : '—'}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
