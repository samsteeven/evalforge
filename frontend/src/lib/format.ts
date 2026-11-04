const number = new Intl.NumberFormat('fr-FR')
const dateTime = new Intl.DateTimeFormat('fr-FR', { dateStyle: 'short', timeStyle: 'medium' })

export function percent(value: number | null | undefined): string {
  return value == null ? '—' : `${Math.round(value * 100)} %`
}

export function milliseconds(value: number | null | undefined): string {
  if (value == null) return '—'
  return value >= 1000 ? `${(value / 1000).toFixed(1).replace('.', ',')} s` : `${Math.round(value)} ms`
}

/** Un coût inconnu reste « inconnu » : jamais affiché comme 0. */
export function usd(value: number | null | undefined): string {
  if (value == null) return 'inconnu'
  if (value === 0) return '0 $'
  // Les coûts d'un run se comptent souvent en millièmes de dollar : deux chiffres significatifs suffisent.
  const text = value < 0.01 ? String(Number(value.toPrecision(2))) : value < 1 ? value.toFixed(4) : value.toFixed(2)
  return `${text.replace('.', ',')} $`
}

export function count(value: number | null | undefined): string {
  return value == null ? '—' : number.format(value)
}

export function when(value: string | null | undefined): string {
  return value ? dateTime.format(new Date(value)) : '—'
}

/** Mêmes règles que le serveur : seules les substitutions `{{ nom }}` sont des variables. */
export function templateVariables(template: string): string[] {
  const names = [...template.matchAll(/\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g)].map((match) => match[1] ?? '')
  return [...new Set(names)]
}

export const RUN_STATUS: Record<string, { label: string; tone: 'neutral' | 'info' | 'success' | 'danger' | 'warning' }> = {
  queued: { label: 'En file', tone: 'neutral' },
  running: { label: 'En cours', tone: 'info' },
  completed: { label: 'Terminé', tone: 'success' },
  failed: { label: 'Échec', tone: 'danger' },
  interrupted: { label: 'Interrompu', tone: 'warning' },
}

export const RESULT_STATUS: Record<string, { label: string; tone: 'success' | 'danger' | 'warning' }> = {
  success: { label: 'Réponse', tone: 'success' },
  error: { label: 'Erreur', tone: 'danger' },
  timeout: { label: 'Délai dépassé', tone: 'warning' },
}

export const ROLE_LABEL: Record<string, string> = {
  owner: 'Propriétaire',
  admin: 'Administrateur',
  member: 'Membre',
  viewer: 'Lecteur',
}
