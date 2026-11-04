import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api, unwrap, unwrapWithStatus } from '../../api/client'
import { useWorkspace } from '../../auth/useAuth'
import { Alert, Badge, Button, Card, Empty, ErrorAlert, Field, Input, Loading, Textarea } from '../../components/ui'
import { templateVariables, when } from '../../lib/format'
import { SAMPLE_TEMPLATE } from '../../lib/sample'

export function PromptsPanel({ projectId }: { projectId: string }) {
  const { canEdit } = useWorkspace()
  const prompts = useQuery({
    queryKey: ['prompts', projectId],
    queryFn: () => unwrap(api.GET('/api/v1/projects/{project_id}/prompts', { params: { path: { project_id: projectId } } })),
  })

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_400px]">
      <div className="space-y-3">
        {prompts.isPending && <Loading />}
        <ErrorAlert error={prompts.error} />
        {prompts.data?.length === 0 && <Empty>Aucun prompt. Chaque modification crée une nouvelle version.</Empty>}
        {[...(prompts.data ?? [])].reverse().map((prompt) => (
          <Card key={prompt.id}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-white">{prompt.name}</span>
              <Badge tone="info">v{prompt.version}</Badge>
              {prompt.variables.map((variable) => (
                <Badge key={variable}>{`{{ ${variable} }}`}</Badge>
              ))}
              <span className="ml-auto text-xs text-slate-500">{when(prompt.created_at)}</span>
            </div>
            {prompt.system && <p className="mt-3 text-xs text-slate-400">Système : {prompt.system}</p>}
            <pre className="mt-3 overflow-x-auto whitespace-pre-wrap rounded-lg bg-slate-950 p-3 font-mono text-xs text-slate-300">{prompt.template}</pre>
          </Card>
        ))}
      </div>
      {canEdit && <CreatePrompt projectId={projectId} />}
    </div>
  )
}

function CreatePrompt({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('faq')
  const [system, setSystem] = useState('')
  const [template, setTemplate] = useState(SAMPLE_TEMPLATE)
  const [notice, setNotice] = useState<string | null>(null)
  const create = useMutation({
    mutationFn: () =>
      unwrapWithStatus(
        api.POST('/api/v1/projects/{project_id}/prompts', {
          params: { path: { project_id: projectId } },
          body: { name, template, system: system || null },
        }),
      ),
    onSuccess: ({ data, status }) => {
      setNotice(status === 201 ? `Version ${data.version} enregistrée.` : `Identique à la version ${data.version}.`)
      void queryClient.invalidateQueries({ queryKey: ['prompts', projectId] })
    },
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    setNotice(null)
    create.mutate()
  }
  const detected = templateVariables(template)

  return (
    <Card className="h-fit">
      <h2 className="mb-4 font-medium">Nouvelle version de prompt</h2>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Nom du prompt" hint="Même nom = nouvelle version du même prompt">
          <Input required value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Message système (facultatif)">
          <Input value={system} onChange={(e) => setSystem(e.target.value)} placeholder="Tu es un agent de support concis." />
        </Field>
        <Field
          label="Gabarit"
          hint={
            <>
              Variables : champs <code>input</code> du cas, et <code>{'{{ context }}'}</code>. Détectées :{' '}
              {detected.length ? detected.join(', ') : 'aucune'}
            </>
          }
        >
          <Textarea rows={7} required value={template} onChange={(e) => setTemplate(e.target.value)} />
        </Field>
        <ErrorAlert error={create.error} />
        {notice && <Alert tone="success">{notice}</Alert>}
        <Button type="submit" busy={create.isPending} className="w-full">Enregistrer la version</Button>
      </form>
    </Card>
  )
}
