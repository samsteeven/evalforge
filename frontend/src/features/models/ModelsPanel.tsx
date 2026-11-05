import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api, unwrap, type Schemas } from '../../api/client'
import { useWorkspace } from '../../auth/useAuth'
import { Badge, Button, Card, Empty, ErrorAlert, Field, Input, Loading, Select } from '../../components/ui'

type Kind = 'openai_compatible' | 'ollama' | 'evalforge_agent'

const KIND_LABEL: Record<string, string> = {
  openai_compatible: 'Compatible OpenAI',
  ollama: 'Ollama',
  evalforge_agent: 'Agent EvalForge',
}
const DEFAULT_URL: Record<Kind, string> = {
  openai_compatible: 'https://api.deepseek.com/v1',
  ollama: 'http://localhost:11434/v1',
  evalforge_agent: 'http://127.0.0.1:8200',
}

export function ModelsPanel({ projectId }: { projectId: string }) {
  const { canAdmin } = useWorkspace()
  const models = useQuery({
    queryKey: ['models', projectId],
    queryFn: () => unwrap(api.GET('/api/v1/projects/{project_id}/providers', { params: { path: { project_id: projectId } } })),
  })

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
      <div className="space-y-3">
        {models.isPending && <Loading />}
        <ErrorAlert error={models.error} />
        {models.data?.length === 0 && <Empty>Aucun modèle configuré.</Empty>}
        {models.data?.map((model) => <ModelCard key={model.id} model={model} />)}
      </div>
      {canAdmin ? (
        <CreateModel projectId={projectId} />
      ) : (
        <Card className="h-fit text-sm text-slate-400">Seuls les administrateurs configurent les modèles et leurs clés d'API.</Card>
      )}
    </div>
  )
}

function ModelCard({ model }: { model: Schemas['ModelConfigurationOut'] }) {
  const pricing = model.pricing as Schemas['Pricing'] | null
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-white">{model.name}</span>
        <Badge>{KIND_LABEL[model.provider_kind] ?? model.provider_kind}</Badge>
        {model.has_api_key ? <Badge tone="success">Clé chiffrée</Badge> : <Badge>Sans clé</Badge>}
      </div>
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-slate-500">{model.provider_kind === 'evalforge_agent' ? 'Version' : 'Modèle'}</dt>
        <dd className="font-mono text-slate-300">{model.model_name}</dd>
        <dt className="text-slate-500">Adresse</dt>
        <dd className="truncate font-mono text-slate-300">{model.base_url}</dd>
        <dt className="text-slate-500">Paramètres</dt>
        <dd className="text-slate-300">
          température {String(model.parameters.temperature)}, max_tokens {String(model.parameters.max_tokens ?? '—')}
        </dd>
        <dt className="text-slate-500">Prix</dt>
        <dd className="text-slate-300">
          {pricing
            ? `${pricing.input_per_million_usd} $ / ${pricing.output_per_million_usd} $ par million de tokens (entrée / sortie)`
            : 'non renseigné : le coût restera inconnu'}
        </dd>
      </dl>
    </Card>
  )
}

function CreateModel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState({
    name: '',
    provider_kind: 'openai_compatible' as Kind,
    base_url: 'https://api.deepseek.com/v1',
    model_name: '',
    temperature: '0',
    max_tokens: '',
    api_key: '',
    input_price: '',
    output_price: '',
    price_source: '',
  })
  const set = (key: keyof typeof form) => (event: { target: { value: string } }) =>
    setForm((current) => ({ ...current, [key]: event.target.value }))

  const create = useMutation({
    mutationFn: () => {
      const priced = form.input_price !== '' && form.output_price !== ''
      return unwrap(
        api.POST('/api/v1/projects/{project_id}/providers', {
          params: { path: { project_id: projectId } },
          body: {
            name: form.name,
            provider_kind: form.provider_kind,
            base_url: form.base_url,
            model_name: form.model_name,
            parameters: { temperature: Number(form.temperature), max_tokens: form.max_tokens ? Number(form.max_tokens) : null },
            api_key: form.api_key || null,
            pricing: priced
              ? {
                  input_per_million_usd: Number(form.input_price),
                  output_per_million_usd: Number(form.output_price),
                  source: form.price_source || null,
                }
              : null,
          },
        }),
      )
    },
    onSuccess: () => {
      setForm((current) => ({ ...current, name: '', model_name: '', api_key: '' }))
      void queryClient.invalidateQueries({ queryKey: ['models', projectId] })
    },
  })
  const agent = form.provider_kind === 'evalforge_agent'
  const submit = (event: FormEvent) => {
    event.preventDefault()
    create.mutate()
  }

  return (
    <Card className="h-fit">
      <h2 className="mb-4 font-medium">Nouveau modèle</h2>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Nom affiché">
          <Input required value={form.name} onChange={set('name')} placeholder="DeepSeek V3" />
        </Field>
        <Field label="Type de fournisseur">
          <Select
            value={form.provider_kind}
            onChange={(e) => {
              const kind = e.target.value as typeof form.provider_kind
              setForm((current) => ({
                ...current,
                provider_kind: kind,
                base_url: DEFAULT_URL[kind],
              }))
            }}
          >
            <option value="openai_compatible">Compatible OpenAI</option>
            <option value="ollama">Ollama</option>
            <option value="evalforge_agent">Agent EvalForge (POST /v1/agent/run)</option>
          </Select>
        </Field>
        <Field label="Adresse de l'API" hint="Les adresses internes sont refusées, sauf configuration explicite du serveur">
          <Input required type="url" value={form.base_url} onChange={set('base_url')} />
        </Field>
        <Field
          label={agent ? "Version de l'agent" : 'Identifiant du modèle'}
          hint={agent ? 'Libre : sert à distinguer les versions comparées' : undefined}
        >
          <Input required value={form.model_name} onChange={set('model_name')} placeholder={agent ? 'v1' : 'deepseek-chat'} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Température">
            <Input type="number" min="0" max="2" step="0.1" value={form.temperature} onChange={set('temperature')} />
          </Field>
          <Field label="max_tokens">
            <Input type="number" min="1" value={form.max_tokens} onChange={set('max_tokens')} />
          </Field>
        </div>
        <Field label="Clé d'API" hint="Chiffrée en base, jamais réaffichée">
          <Input type="password" autoComplete="off" value={form.api_key} onChange={set('api_key')} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Prix entrée ($/M)">
            <Input type="number" min="0" step="any" value={form.input_price} onChange={set('input_price')} />
          </Field>
          <Field label="Prix sortie ($/M)">
            <Input type="number" min="0" step="any" value={form.output_price} onChange={set('output_price')} />
          </Field>
        </div>
        <Field label="Source du prix" hint="Page tarifaire et date : sans prix, le coût reste « inconnu »">
          <Input value={form.price_source} onChange={set('price_source')} />
        </Field>
        <ErrorAlert error={create.error} />
        <Button type="submit" busy={create.isPending} className="w-full">Ajouter le modèle</Button>
      </form>
    </Card>
  )
}
