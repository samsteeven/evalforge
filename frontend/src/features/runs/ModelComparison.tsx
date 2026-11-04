import type { Schemas } from '../../api/client'
import { count, milliseconds, percent, usd } from '../../lib/format'

const COLORS = ['bg-indigo-400', 'bg-emerald-400', 'bg-amber-400', 'bg-sky-400', 'bg-rose-400']

/** Qualité d'un côté, performance et coût de l'autre : une latence ne se compare pas à un taux de réussite. */
export function ModelComparison({ models }: { models: Schemas['ModelSummary'][] }) {
  const metricNames = [...new Set(models.flatMap((m) => m.metrics.map((metric) => metric.name)))]

  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <section aria-label="Qualité" className="rounded-xl border border-slate-800 bg-slate-900/60 p-5">
        <h2 className="mb-1 font-medium">Qualité</h2>
        <p className="mb-4 text-xs text-slate-500">Taux de réussite par métrique, sur les réponses obtenues</p>
        <div className="space-y-5">
          {metricNames.map((name) => (
            <div key={name}>
              <p className="mb-2 font-mono text-xs text-slate-400">{name}</p>
              <div className="space-y-1.5">
                {models.map((model, index) => {
                  const summary = model.metrics.find((m) => m.name === name)
                  const rate = summary?.pass_rate ?? null
                  return (
                    <div key={model.model_configuration_id} className="grid grid-cols-[120px_1fr_56px] items-center gap-3 text-sm">
                      <span className="truncate text-slate-300">{model.model}</span>
                      <div className="h-2.5 rounded-full bg-slate-800" role="meter" aria-label={`${name} ${model.model}`} aria-valuenow={rate == null ? undefined : Math.round(rate * 100)} aria-valuemin={0} aria-valuemax={100}>
                        {rate != null && rate > 0 && (
                          <div className={`h-2.5 rounded-full ${COLORS[index % COLORS.length]}`} style={{ width: `${rate * 100}%` }} />
                        )}
                      </div>
                      <span className="text-right tabular-nums text-slate-200">{percent(rate)}</span>
                    </div>
                  )
                })}
              </div>
              {models.some((m) => (m.metrics.find((x) => x.name === name)?.unavailable ?? 0) > 0) && (
                <p className="mt-1 text-xs text-amber-300/80">Certaines réponses n'ont pas pu être évaluées : elles ne comptent pas comme des échecs.</p>
              )}
            </div>
          ))}
        </div>
      </section>

      <section aria-label="Performance et coût" className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-900/60 p-5">
        <h2 className="mb-1 font-medium">Performance et coût</h2>
        <p className="mb-4 text-xs text-slate-500">Latence mesurée côté EvalForge, tokens annoncés par le fournisseur</p>
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-slate-500">
            <tr>
              <th className="pb-2 font-medium">Modèle</th>
              <th className="pb-2 font-medium">Réussite</th>
              <th className="pb-2 font-medium">P50</th>
              <th className="pb-2 font-medium">P95</th>
              <th className="pb-2 font-medium">Tokens</th>
              <th className="pb-2 font-medium">Coût</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {models.map((model) => {
              const p = model.performance
              return (
                <tr key={model.model_configuration_id}>
                  <td className="py-2 text-slate-200">{model.model}</td>
                  <td className="py-2 tabular-nums">
                    {percent(p.success_rate)}
                    {p.errors + p.timeouts > 0 && <span className="ml-1 text-xs text-rose-300">({p.errors + p.timeouts} échec)</span>}
                  </td>
                  <td className="py-2 tabular-nums">{milliseconds(p.p50_latency_ms)}</td>
                  <td className="py-2 tabular-nums">{milliseconds(p.p95_latency_ms)}</td>
                  <td className="py-2 tabular-nums">{count(p.input_tokens)} / {count(p.output_tokens)}</td>
                  <td className="py-2 tabular-nums" title={p.cost_usd == null ? `Prix connu pour ${p.cost_known_for} réponse(s) sur ${p.succeeded}` : undefined}>
                    {usd(p.cost_usd)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </section>
    </div>
  )
}
