type Step = { [key: string]: unknown }

const STATUS_LABEL: Record<string, string> = {
  success: 'réussi',
  invalid_arguments: 'arguments invalides',
  unknown_tool: 'outil inconnu',
  error: 'erreur',
}

/** Les appels d'outils d'un agent, dans l'ordre : ce que la réponse finale ne montre pas. */
export function Trajectory({ steps, stopReason }: { steps: Step[]; stopReason?: string | null }) {
  return (
    <ol aria-label="Trajectoire de l'agent" className="mt-2 flex flex-wrap items-center gap-1 text-xs">
      {steps.length === 0 && <li className="text-slate-500">aucun outil appelé</li>}
      {steps.map((step, index) => {
        const ok = step.status === 'success'
        const status = String(step.status ?? '')
        const args = typeof step.arguments === 'string' ? step.arguments : JSON.stringify(step.arguments ?? {})
        return (
          <li key={index} className="flex items-center gap-1">
            {index > 0 && <span aria-hidden="true" className="text-slate-600">→</span>}
            <span
              title={`${args}${step.error ? ` : ${String(step.error)}` : ''}`}
              className={
                'rounded px-1.5 py-0.5 font-mono ' +
                (ok ? 'bg-slate-800 text-slate-300' : 'bg-rose-950 text-rose-200 ring-1 ring-rose-800')
              }
            >
              {String(step.tool)}
              {!ok && ` (${STATUS_LABEL[status] ?? status})`}
            </span>
          </li>
        )
      })}
      {stopReason && stopReason !== 'completed' && <li className="text-amber-300">arrêt : {stopReason}</li>}
    </ol>
  )
}
