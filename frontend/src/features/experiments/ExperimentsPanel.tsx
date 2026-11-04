import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { api, unwrap, type Schemas } from '../../api/client'
import { useWorkspace } from '../../auth/useAuth'
import { Badge, Button, Card, Empty, ErrorAlert, Field, Input, Loading, Select } from '../../components/ui'
import { RUN_STATUS, when } from '../../lib/format'

type VersionChoice = { id: string; label: string }

/** Toutes les versions de tous les jeux du projet : un run vise toujours une version immuable. */
function useDatasetVersions(projectId: string) {
  return useQuery({
    queryKey: ['all-versions', projectId],
    queryFn: async (): Promise<VersionChoice[]> => {
      const datasets = await unwrap(
        api.GET('/api/v1/projects/{project_id}/datasets', { params: { path: { project_id: projectId } } }),
      )
      const perDataset = await Promise.all(
        datasets.map(async (dataset) => {
          const versions = await unwrap(
            api.GET('/api/v1/datasets/{dataset_id}/versions', { params: { path: { dataset_id: dataset.id } } }),
          )
          return versions
            .reverse()
            .map((v) => ({ id: v.id, label: `${dataset.name} · v${v.version} (${v.case_count} cas)` }))
        }),
      )
      return perDataset.flat()
    },
  })
}

export function ExperimentsPanel({ projectId }: { projectId: string }) {
  const { canEdit } = useWorkspace()
  const experiments = useQuery({
    queryKey: ['experiments', projectId],
    queryFn: () => unwrap(api.GET('/api/v1/projects/{project_id}/experiments', { params: { path: { project_id: projectId } } })),
  })
  const prompts = useQuery({
    queryKey: ['prompts', projectId],
    queryFn: () => unwrap(api.GET('/api/v1/projects/{project_id}/prompts', { params: { path: { project_id: projectId } } })),
  })
  const versions = useDatasetVersions(projectId)
  const promptLabel = (id: string) => {
    const prompt = prompts.data?.find((p) => p.id === id)
    return prompt ? `${prompt.name} v${prompt.version}` : '…'
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
      <div className="space-y-4">
        {experiments.isPending && <Loading />}
        <ErrorAlert error={experiments.error} />
        {experiments.data?.length === 0 && <Empty>Aucune expérience : un prompt, des modèles et des métriques à comparer.</Empty>}
        {[...(experiments.data ?? [])].reverse().map((experiment) => (
          <ExperimentCard
            key={experiment.id}
            experiment={experiment}
            promptLabel={promptLabel(experiment.prompt_version_id)}
            versions={versions.data ?? []}
            canEdit={canEdit}
          />
        ))}
      </div>
      {canEdit && <CreateExperiment projectId={projectId} prompts={prompts.data ?? []} />}
    </div>
  )
}

function ExperimentCard({
  experiment,
  promptLabel,
  versions,
  canEdit,
}: {
  experiment: Schemas['ExperimentOut']
  promptLabel: string
  versions: VersionChoice[]
  canEdit: boolean
}) {
  const runs = useQuery({
    queryKey: ['runs', experiment.id],
    queryFn: () => unwrap(api.GET('/api/v1/experiments/{experiment_id}/runs', { params: { path: { experiment_id: experiment.id } } })),
  })

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-medium text-white">{experiment.name}</h3>
        <Badge tone="info">{promptLabel}</Badge>
      </div>
      <p className="mt-2 text-sm text-slate-400">
        {experiment.models.map((m) => m.name).join(' vs ')} · {experiment.metrics.join(', ')}
        {experiment.judge && <> · juge : {experiment.judge.name}</>}
      </p>
      {canEdit && <LaunchRun experimentId={experiment.id} versions={versions} />}
      <div className="mt-4">
        <h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">Runs</h4>
        {runs.data?.length === 0 && <p className="text-sm text-slate-500">Aucun run.</p>}
        <ul className="divide-y divide-slate-800">
          {runs.data?.map((run) => {
            const status = RUN_STATUS[run.status] ?? { label: run.status, tone: 'neutral' as const }
            return (
              <li key={run.id}>
                <Link to={`/runs/${run.id}`} className="flex items-center gap-3 py-2 text-sm hover:text-white">
                  <Badge tone={status.tone}>{status.label}</Badge>
                  <span className="text-slate-300">{run.completed_items} / {run.total_items} réponses</span>
                  <span className="ml-auto text-xs text-slate-500">{when(run.created_at)}</span>
                </Link>
              </li>
            )
          })}
        </ul>
      </div>
    </Card>
  )
}

function LaunchRun({ experimentId, versions }: { experimentId: string; versions: VersionChoice[] }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [versionId, setVersionId] = useState('')
  const [concurrency, setConcurrency] = useState('5')
  const [timeout, setTimeoutSeconds] = useState('60')
  const selected = versionId || versions[0]?.id || ''
  const launch = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/experiments/{experiment_id}/runs', {
          params: { path: { experiment_id: experimentId } },
          body: {
            dataset_version_id: selected,
            execution: { max_concurrency: Number(concurrency), timeout_seconds: Number(timeout) },
          },
        }),
      ),
    onSuccess: (run) => {
      void queryClient.invalidateQueries({ queryKey: ['runs', experimentId] })
      void navigate(`/runs/${run.run_id}`)
    },
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    launch.mutate()
  }

  if (versions.length === 0) return <p className="mt-4 text-sm text-slate-500">Importez une version de jeu de données pour lancer un run.</p>
  return (
    <form onSubmit={submit} className="mt-4 grid gap-3 rounded-lg border border-slate-800 p-3 sm:grid-cols-[1fr_110px_110px_auto] sm:items-end">
      <Field label="Version du jeu">
        <Select value={selected} onChange={(e) => setVersionId(e.target.value)}>
          {versions.map((v) => (
            <option key={v.id} value={v.id}>{v.label}</option>
          ))}
        </Select>
      </Field>
      <Field label="Parallélisme">
        <Input type="number" min="1" max="20" value={concurrency} onChange={(e) => setConcurrency(e.target.value)} />
      </Field>
      <Field label="Délai (s)">
        <Input type="number" min="1" max="600" value={timeout} onChange={(e) => setTimeoutSeconds(e.target.value)} />
      </Field>
      <Button type="submit" busy={launch.isPending}>Lancer le run</Button>
      <div className="sm:col-span-4">
        <ErrorAlert error={launch.error} />
      </div>
    </form>
  )
}

function CreateExperiment({ projectId, prompts }: { projectId: string; prompts: Schemas['PromptOut'][] }) {
  const queryClient = useQueryClient()
  const models = useQuery({
    queryKey: ['models', projectId],
    queryFn: () => unwrap(api.GET('/api/v1/projects/{project_id}/providers', { params: { path: { project_id: projectId } } })),
  })
  const metrics = useQuery({ queryKey: ['metrics'], queryFn: () => unwrap(api.GET('/api/v1/metrics')) })
  const [name, setName] = useState('')
  const [promptId, setPromptId] = useState('')
  const [modelIds, setModelIds] = useState<string[]>([])
  const [metricNames, setMetricNames] = useState<string[]>([])
  const [judgeId, setJudgeId] = useState('')
  const latestPrompt = prompts.at(-1)?.id ?? ''
  const toggle = (list: string[], value: string) => (list.includes(value) ? list.filter((v) => v !== value) : [...list, value])
  const judgeNeeded = metricNames.some((name) => metrics.data?.find((m) => m.name === name)?.needs_judge)

  const create = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/projects/{project_id}/experiments', {
          params: { path: { project_id: projectId } },
          body: {
            name,
            prompt_version_id: promptId || latestPrompt,
            model_configuration_ids: modelIds,
            metrics: metricNames,
            judge_model_configuration_id: judgeNeeded ? judgeId || null : null,
          },
        }),
      ),
    onSuccess: () => {
      setName('')
      void queryClient.invalidateQueries({ queryKey: ['experiments', projectId] })
    },
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    create.mutate()
  }

  return (
    <Card className="h-fit">
      <h2 className="mb-4 font-medium">Nouvelle expérience</h2>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Nom de l'expérience">
          <Input required value={name} onChange={(e) => setName(e.target.value)} placeholder="DeepSeek contre Llama" />
        </Field>
        <Field label="Version du prompt">
          <Select required value={promptId || latestPrompt} onChange={(e) => setPromptId(e.target.value)}>
            {prompts.length === 0 && <option value="">Aucun prompt</option>}
            {prompts.map((p) => (
              <option key={p.id} value={p.id}>{`${p.name} v${p.version}`}</option>
            ))}
          </Select>
        </Field>
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-slate-300">Modèles comparés (5 au plus)</legend>
          {models.data?.length === 0 && <p className="text-sm text-slate-500">Aucun modèle configuré.</p>}
          {models.data?.map((model) => (
            <label key={model.id} className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={modelIds.includes(model.id)} onChange={() => setModelIds(toggle(modelIds, model.id))} />
              {model.name} <span className="text-slate-500">({model.model_name})</span>
            </label>
          ))}
        </fieldset>
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-slate-300">Métriques</legend>
          {metrics.data?.map((metric) => (
            <label key={metric.name} className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                checked={metricNames.includes(metric.name)}
                onChange={() => setMetricNames(toggle(metricNames, metric.name))}
              />
              <span>
                <code>{metric.name}</code>
                {metric.needs_judge && (
                  <span className="ml-2">
                    <Badge tone="warning">juge LLM</Badge>
                  </span>
                )}
                <span className="block text-xs text-slate-500">{metric.description}</span>
              </span>
            </label>
          ))}
        </fieldset>
        {judgeNeeded && (
          <Field
            label="Modèle juge"
            hint="Il note chaque réponse selon une grille versionnée ; ses notes sont des avis justifiés, pas une vérité."
          >
            <Select required value={judgeId} onChange={(e) => setJudgeId(e.target.value)}>
              <option value="">Choisir un modèle</option>
              {models.data?.map((model) => (
                <option key={model.id} value={model.id}>{`${model.name} (${model.model_name})`}</option>
              ))}
            </Select>
          </Field>
        )}
        <ErrorAlert error={create.error} />
        <Button
          type="submit"
          busy={create.isPending}
          className="w-full"
          disabled={!modelIds.length || !metricNames.length || (judgeNeeded && !judgeId)}
        >
          Créer l'expérience
        </Button>
      </form>
    </Card>
  )
}
