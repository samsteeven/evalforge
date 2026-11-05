import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError, unwrap, type Schemas } from '../../api/client'
import { useWorkspace } from '../../auth/useAuth'
import { Alert, Badge, Button, Card, ErrorAlert } from '../../components/ui'
import { percent } from '../../lib/format'

/** Baseline du projet : adopter ce run, ou voir ce qui a changé par rapport à elle. */
export function BaselinePanel({ projectId, runId, completed }: { projectId: string; runId: string; completed: boolean }) {
  const { canEdit } = useWorkspace()
  const queryClient = useQueryClient()
  const baseline = useQuery({
    queryKey: ['baseline', projectId],
    queryFn: () => unwrap(api.GET('/api/v1/projects/{project_id}/baseline', { params: { path: { project_id: projectId } } })),
  })
  const isBaseline = baseline.data?.run_id === runId
  const comparison = useQuery({
    queryKey: ['comparison', runId, baseline.data?.run_id],
    queryFn: () => unwrap(api.GET('/api/v1/runs/{run_id}/comparison', { params: { path: { run_id: runId } } })),
    enabled: completed && !!baseline.data && !isBaseline,
    retry: false,
  })
  const adopt = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT('/api/v1/projects/{project_id}/baseline', {
          params: { path: { project_id: projectId } },
          body: { run_id: runId, tolerance: baseline.data?.tolerance ?? 0.02 },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['baseline', projectId] }),
  })

  if (!completed || baseline.isPending) return null

  return (
    <Card className="mb-6">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="font-medium">Baseline</h2>
        {isBaseline ? (
          <Badge tone="info">Ce run est la baseline du projet</Badge>
        ) : (
          !baseline.data && <span className="text-sm text-slate-400">Aucune baseline : ce run peut servir de référence.</span>
        )}
        {canEdit && !isBaseline && (
          <Button variant="secondary" className="ml-auto" busy={adopt.isPending} onClick={() => adopt.mutate()}>
            Adopter comme baseline
          </Button>
        )}
      </div>
      <ErrorAlert error={adopt.error} />
      {comparison.error instanceof ApiError && comparison.error.status === 422 && (
        <p className="mt-3 text-sm text-slate-400">{comparison.error.message}</p>
      )}
      {comparison.data && <ComparisonView data={comparison.data} />}
    </Card>
  )
}

function delta(value: number | null | undefined): string {
  if (value == null) return '—'
  const points = Math.round(value * 100)
  return `${points > 0 ? '+' : ''}${points} pts`
}

function ComparisonView({ data }: { data: Schemas['ComparisonOut'] }) {
  const { comparison } = data
  const broken = comparison.changes.filter((c) => c.change === 'broken')
  return (
    <section className="mt-4 space-y-4" aria-label="Comparaison avec la baseline">
      {comparison.regressed ? (
        <Alert>Régression détectée face à la baseline (tolérance {percent(comparison.tolerance)}).</Alert>
      ) : (
        <Alert tone="success">Aucune régression face à la baseline (tolérance {percent(comparison.tolerance)}).</Alert>
      )}
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-slate-500">
          <tr>
            <th className="pb-2 font-medium">Modèle</th>
            <th className="pb-2 font-medium">Métrique</th>
            <th className="pb-2 text-right font-medium">Baseline</th>
            <th className="pb-2 text-right font-medium">Ce run</th>
            <th className="pb-2 text-right font-medium">Écart</th>
            <th className="pb-2 pr-6 text-right font-medium">Cassés / réparés</th>
            <th className="pb-2 font-medium">Test apparié</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800">
          {comparison.models.flatMap((model) =>
            model.metrics.map((m) => (
              <tr key={`${model.model}-${m.name}`} data-testid="comparison-row">
                <td className="py-2">{model.model}</td>
                <td className="py-2 font-mono text-xs">{m.name}</td>
                <td className="py-2 text-right tabular-nums">{percent(m.baseline_rate)}</td>
                <td className="py-2 text-right tabular-nums">{percent(m.candidate_rate)}</td>
                <td
                  className={
                    'py-2 text-right tabular-nums ' + (m.regressed ? 'text-rose-300' : m.improved ? 'text-emerald-300' : 'text-slate-300')
                  }
                >
                  {delta(m.delta)}
                </td>
                <td className="py-2 pr-6 text-right tabular-nums">
                  {m.broken} / {m.fixed}
                </td>
                <td className="py-2 text-xs text-slate-400">
                  {m.p_value == null
                    ? 'aucun cas discordant'
                    : `p = ${m.p_value.toFixed(3).replace('.', ',')} · ${m.significant ? 'significatif' : 'non significatif'}`}
                </td>
              </tr>
            )),
          )}
        </tbody>
      </table>
      {broken.length > 0 && (
        <div>
          <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">Cas cassés</h3>
          <ul className="space-y-1 text-sm">
            {broken.map((c) => (
              <li key={`${c.case_id}-${c.model}-${c.metric}`}>
                <code className="text-xs text-slate-400">{c.case_id}</code> · {c.model} · <code className="text-xs">{c.metric}</code>
                {c.candidate_reason && <span className="text-slate-500"> : {c.candidate_reason}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {(comparison.only_in_baseline.length > 0 || comparison.only_in_candidate.length > 0) && (
        <p className="text-xs text-slate-500">
          Modèles non comparés : {[...comparison.only_in_baseline, ...comparison.only_in_candidate].join(', ')}
        </p>
      )}
    </section>
  )
}
