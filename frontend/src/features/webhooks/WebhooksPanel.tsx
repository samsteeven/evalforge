import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api, unwrap, type Schemas } from '../../api/client'
import { useWorkspace } from '../../auth/useAuth'
import { Alert, Badge, Button, Card, Empty, ErrorAlert, Field, Input, Loading } from '../../components/ui'
import { when } from '../../lib/format'

type EventName = 'evaluation.completed' | 'evaluation.failed'

const EVENTS: { id: EventName; label: string }[] = [
  { id: 'evaluation.completed', label: 'Run terminé' },
  { id: 'evaluation.failed', label: 'Run en échec' },
]

const DELIVERY_STATUS: Record<string, { label: string; tone: 'success' | 'danger' | 'warning' }> = {
  delivered: { label: 'Livré', tone: 'success' },
  failed: { label: 'Échec', tone: 'danger' },
  pending: { label: 'En reprise', tone: 'warning' },
}

/** Intégrations : prévenir un outil externe (CI, messagerie d'équipe) à chaque fin de run, avec le résumé signé. */
export function WebhooksPanel({ projectId }: { projectId: string }) {
  const { canAdmin } = useWorkspace()
  const hooks = useQuery({
    queryKey: ['webhooks', projectId],
    queryFn: () => unwrap(api.GET('/api/v1/projects/{project_id}/webhooks', { params: { path: { project_id: projectId } } })),
    enabled: canAdmin,
  })

  if (!canAdmin) {
    return <Card className="text-sm text-slate-400">Seuls les administrateurs gèrent les webhooks : leur URL peut porter un jeton.</Card>
  }
  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
      <div className="space-y-3">
        {hooks.isPending && <Loading />}
        <ErrorAlert error={hooks.error} />
        {hooks.data?.length === 0 && <Empty>Aucun webhook : personne n'est prévenu des fins de run.</Empty>}
        {hooks.data?.map((hook) => <WebhookCard key={hook.id} hook={hook} projectId={projectId} />)}
      </div>
      <CreateWebhook projectId={projectId} />
    </div>
  )
}

function WebhookCard({ hook, projectId }: { hook: Schemas['WebhookOut']; projectId: string }) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const remove = useMutation({
    mutationFn: () => unwrap(api.DELETE('/api/v1/webhooks/{webhook_id}', { params: { path: { webhook_id: hook.id } } })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['webhooks', projectId] }),
  })
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 truncate font-mono text-sm text-white" title={hook.url}>{hook.url}</span>
        {hook.events.map((event) => (
          <Badge key={event}>{EVENTS.find((e) => e.id === event)?.label ?? event}</Badge>
        ))}
      </div>
      <div className="mt-3 flex gap-2">
        <Button variant="secondary" onClick={() => setOpen((current) => !current)} aria-expanded={open}>
          {open ? 'Masquer les livraisons' : 'Livraisons'}
        </Button>
        <Button
          variant="secondary"
          busy={remove.isPending}
          onClick={() => {
            if (window.confirm('Supprimer ce webhook ? Son historique de livraisons sera effacé.')) remove.mutate()
          }}
        >
          Supprimer
        </Button>
      </div>
      <ErrorAlert error={remove.error} />
      {open && <Deliveries webhookId={hook.id} />}
    </Card>
  )
}

function Deliveries({ webhookId }: { webhookId: string }) {
  const deliveries = useQuery({
    queryKey: ['webhook-deliveries', webhookId],
    queryFn: () => unwrap(api.GET('/api/v1/webhooks/{webhook_id}/deliveries', { params: { path: { webhook_id: webhookId } } })),
    // Relu tant qu'une livraison est en reprise, ou qu'aucune n'est encore arrivée (fin de run toute récente).
    refetchInterval: (query) => {
      const data = query.state.data
      return !data || data.length === 0 || data.some((d) => d.status === 'pending') ? 2000 : false
    },
  })
  if (deliveries.isPending) return <Loading />
  if (deliveries.error) return <ErrorAlert error={deliveries.error} />
  if (deliveries.data.length === 0) return <p className="mt-3 text-sm text-slate-500">Aucune livraison pour l'instant.</p>
  return (
    <table className="mt-3 w-full text-sm">
      <thead className="text-left text-xs text-slate-500">
        <tr>
          <th className="pb-2 font-medium">Date</th>
          <th className="pb-2 font-medium">Événement</th>
          <th className="pb-2 font-medium">État</th>
          <th className="pb-2 text-right font-medium">Tentatives</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-slate-800">
        {deliveries.data.map((delivery) => {
          const status = DELIVERY_STATUS[delivery.status] ?? { label: delivery.status, tone: 'warning' as const }
          return (
            <tr key={delivery.id} data-testid="delivery-row">
              <td className="py-2 pr-3 whitespace-nowrap text-slate-400">{when(delivery.created_at)}</td>
              <td className="py-2 pr-3 font-mono text-xs">{delivery.event_type}</td>
              <td className="py-2 pr-3">
                <Badge tone={status.tone}>{status.label}</Badge>
                {delivery.last_error && <span className="ml-2 text-xs text-rose-200">{delivery.last_error}</span>}
              </td>
              <td className="py-2 text-right tabular-nums">{delivery.attempts}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function CreateWebhook({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient()
  const [url, setUrl] = useState('')
  const [events, setEvents] = useState<EventName[]>(['evaluation.completed', 'evaluation.failed'])
  const create = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/projects/{project_id}/webhooks', {
          params: { path: { project_id: projectId } },
          body: { url, events },
        }),
      ),
    onSuccess: () => {
      setUrl('')
      void queryClient.invalidateQueries({ queryKey: ['webhooks', projectId] })
    },
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    create.mutate()
  }
  const toggle = (id: EventName) =>
    setEvents((current) => (current.includes(id) ? current.filter((e) => e !== id) : [...current, id]))

  return (
    <Card className="h-fit">
      <h2 className="mb-4 font-medium">Nouveau webhook</h2>
      <form onSubmit={submit} className="space-y-4">
        <Field label="URL du destinataire" hint="HTTPS conseillé ; les adresses internes sont refusées">
          <Input required type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://ci.example.com/hooks/evalforge" />
        </Field>
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-slate-300">Événements</legend>
          {EVENTS.map((event) => (
            <label key={event.id} className="flex items-center gap-2 text-sm text-slate-300">
              <input type="checkbox" checked={events.includes(event.id)} onChange={() => toggle(event.id)} />
              {event.label} <span className="font-mono text-xs text-slate-500">{event.id}</span>
            </label>
          ))}
        </fieldset>
        <ErrorAlert error={create.error} />
        {create.data && (
          <Alert tone="success">
            <p>Webhook créé. Secret de signature, affiché une seule fois :</p>
            <code data-testid="webhook-secret" className="mt-1 block break-all font-mono text-xs">{create.data.secret}</code>
            <p className="mt-1 text-xs">
              Chaque appel porte X-EvalForge-Signature = sha256 HMAC de « horodatage.corps » ; refuser un horodatage
              de plus de 5 minutes.
            </p>
          </Alert>
        )}
        <Button type="submit" busy={create.isPending} disabled={events.length === 0} className="w-full">
          Ajouter le webhook
        </Button>
      </form>
    </Card>
  )
}
