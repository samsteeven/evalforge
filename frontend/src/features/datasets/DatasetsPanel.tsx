import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type ChangeEvent, type FormEvent } from 'react'
import { api, unwrap, unwrapWithStatus, type Schemas } from '../../api/client'
import { useWorkspace } from '../../auth/useAuth'
import { Alert, Badge, Button, Card, Empty, ErrorAlert, Field, Input, Loading, Textarea } from '../../components/ui'
import { when } from '../../lib/format'
import { SAMPLE_CASES } from '../../lib/sample'

export function DatasetsPanel({ projectId }: { projectId: string }) {
  const { canEdit } = useWorkspace()
  const datasets = useQuery({
    queryKey: ['datasets', projectId],
    queryFn: () => unwrap(api.GET('/api/v1/projects/{project_id}/datasets', { params: { path: { project_id: projectId } } })),
  })

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
      <div className="space-y-4">
        {datasets.isPending && <Loading />}
        <ErrorAlert error={datasets.error} />
        {datasets.data?.length === 0 && <Empty>Aucun jeu de données. Créez-en un, puis importez sa première version.</Empty>}
        {datasets.data?.map((dataset) => <DatasetCard key={dataset.id} dataset={dataset} canEdit={canEdit} />)}
      </div>
      {canEdit && <CreateDataset projectId={projectId} />}
    </div>
  )
}

function CreateDataset({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const create = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/projects/{project_id}/datasets', {
          params: { path: { project_id: projectId } },
          body: { name, description: description || null },
        }),
      ),
    onSuccess: () => {
      setName('')
      setDescription('')
      void queryClient.invalidateQueries({ queryKey: ['datasets', projectId] })
    },
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    create.mutate()
  }

  return (
    <Card className="h-fit">
      <h2 className="mb-4 font-medium">Nouveau jeu de données</h2>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Nom du jeu">
          <Input required value={name} onChange={(e) => setName(e.target.value)} placeholder="FAQ support" />
        </Field>
        <Field label="Description">
          <Input value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <ErrorAlert error={create.error} />
        <Button type="submit" busy={create.isPending} className="w-full">Créer le jeu</Button>
      </form>
    </Card>
  )
}

function DatasetCard({ dataset, canEdit }: { dataset: Schemas['DatasetOut']; canEdit: boolean }) {
  const versions = useQuery({
    queryKey: ['versions', dataset.id],
    queryFn: () => unwrap(api.GET('/api/v1/datasets/{dataset_id}/versions', { params: { path: { dataset_id: dataset.id } } })),
  })

  return (
    <Card>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="font-medium text-white">{dataset.name}</h3>
          {dataset.description && <p className="mt-1 text-sm text-slate-400">{dataset.description}</p>}
        </div>
      </div>
      <ErrorAlert error={versions.error} />
      {versions.data && versions.data.length > 0 ? (
        <ul className="mt-4 divide-y divide-slate-800 rounded-lg border border-slate-800">
          {[...versions.data].reverse().map((version) => (
            <li key={version.id} className="flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
              <Badge tone="info">v{version.version}</Badge>
              <span>{version.case_count} cas</span>
              <code className="text-xs text-slate-500" title={version.checksum}>sha256 {version.checksum.slice(0, 12)}</code>
              <span className="ml-auto text-xs text-slate-500">{when(version.created_at)}</span>
            </li>
          ))}
        </ul>
      ) : (
        !versions.isPending && <p className="mt-3 text-sm text-slate-500">Pas encore de version.</p>
      )}
      {canEdit && <ImportVersion datasetId={dataset.id} />}
    </Card>
  )
}

function parseCases(text: string): Schemas['TestCaseIn'][] {
  const parsed: unknown = JSON.parse(text)
  const cases = Array.isArray(parsed) ? parsed : (parsed as { cases?: unknown }).cases
  if (!Array.isArray(cases)) throw new Error('Le JSON doit être une liste de cas, ou un objet { "cases": [...] }')
  return cases as Schemas['TestCaseIn'][]
}

function ImportVersion({ datasetId }: { datasetId: string }) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const importVersion = useMutation({
    // Un fichier (JSON ou CSV) part tel quel : le serveur le lit et le valide. Sinon, le JSON collé.
    mutationFn: async () =>
      file
        ? unwrapWithStatus(
            api.POST('/api/v1/datasets/{dataset_id}/versions/import', {
              params: { path: { dataset_id: datasetId } },
              body: { file: file as unknown as string },
              bodySerializer: () => {
                const form = new FormData()
                form.append('file', file)
                return form
              },
            }),
          )
        : unwrapWithStatus(
            api.POST('/api/v1/datasets/{dataset_id}/versions', {
              params: { path: { dataset_id: datasetId } },
              body: { cases: parseCases(text) },
            }),
          ),
    // La confirmation n'apparaît qu'une fois la liste des versions rechargée : jamais « créée » à côté d'une liste vide.
    onSuccess: async ({ data, status }) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['versions', datasetId] }),
        queryClient.invalidateQueries({ queryKey: ['all-versions'] }),
      ])
      setNotice(
        status === 201
          ? `Version ${data.version} créée : ${data.case_count} cas.`
          : `Contenu identique à la version ${data.version} : aucune nouvelle version.`,
      )
      setText('')
      setFile(null)
      setOpen(false) // le message de confirmation s'affiche à côté du bouton d'import
    },
  })
  const chooseFile = (event: ChangeEvent<HTMLInputElement>) => setFile(event.target.files?.[0] ?? null)
  const submit = (event: FormEvent) => {
    event.preventDefault()
    setNotice(null)
    importVersion.mutate()
  }

  if (!open) {
    return (
      <div className="mt-4 flex items-center gap-3">
        <Button variant="secondary" onClick={() => setOpen(true)}>Importer une version</Button>
        {notice && <Alert tone="success">{notice}</Alert>}
      </div>
    )
  }
  return (
    <form onSubmit={submit} className="mt-4 space-y-3 rounded-lg border border-slate-800 p-4">
      <Field
        label="Fichier JSON ou CSV"
        hint="CSV : colonnes question, expected_answer, context (passages séparés par ||), metadata.<nom> ; séparateur virgule ou point-virgule. 2 Mo, 1 000 cas."
      >
        <input
          type="file"
          accept=".json,.csv,application/json,text/csv"
          onChange={chooseFile}
          className="block text-sm text-slate-400"
        />
      </Field>
      {!file && (
        <>
          <Field label="Ou cas de test collés (JSON)" hint="Chaque cas : input, expected.answer, context, metadata. Une version est immuable.">
            <Textarea rows={10} required value={text} onChange={(e) => setText(e.target.value)} aria-label="Cas de test (JSON)" />
          </Field>
          <Button type="button" variant="ghost" onClick={() => setText(JSON.stringify(SAMPLE_CASES, null, 2))}>
            Charger l'exemple
          </Button>
        </>
      )}
      <ErrorAlert error={importVersion.error} />
      <div className="flex gap-2">
        <Button type="submit" busy={importVersion.isPending}>Créer la version</Button>
        <Button type="button" variant="ghost" onClick={() => setOpen(false)}>Annuler</Button>
      </div>
    </form>
  )
}
