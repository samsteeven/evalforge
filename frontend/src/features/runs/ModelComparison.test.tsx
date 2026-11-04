import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { Schemas } from '../../api/client'
import { ModelComparison } from './ModelComparison'

function summary(model: string, passRate: number | null, unavailable: number, cost: number | null): Schemas['ModelSummary'] {
  return {
    model_configuration_id: `${model}-id`,
    model,
    metrics: [{ name: 'exact_match', evaluated: 5 - unavailable, unavailable, mean_score: passRate, pass_rate: passRate }],
    performance: {
      total: 5,
      succeeded: 5,
      errors: 0,
      timeouts: 0,
      success_rate: 1,
      p50_latency_ms: 120,
      p95_latency_ms: 480,
      input_tokens: 400,
      output_tokens: 60,
      cost_usd: cost,
      cost_known_for: cost == null ? 0 : 5,
    },
  }
}

describe('ModelComparison', () => {
  it('compare la qualité et sépare le coût inconnu d’un coût nul', () => {
    render(<ModelComparison models={[summary('deepseek', 1, 0, 0.0021), summary('llama', 0.4, 0, null)]} />)

    expect(screen.getByRole('meter', { name: 'exact_match deepseek' })).toHaveAttribute('aria-valuenow', '100')
    expect(screen.getByRole('meter', { name: 'exact_match llama' })).toHaveAttribute('aria-valuenow', '40')
    const table = within(screen.getByRole('region', { name: 'Performance et coût' }))
    expect(table.getByText('0,0021 $')).toBeInTheDocument()
    expect(table.getByText('inconnu')).toBeInTheDocument()
    expect(table.getAllByText('480 ms')).toHaveLength(2)
  })

  it('signale une métrique indisponible au lieu de la compter comme un échec', () => {
    render(<ModelComparison models={[summary('deepseek', null, 5, null)]} />)

    expect(screen.getByRole('meter', { name: 'exact_match deepseek' })).not.toHaveAttribute('aria-valuenow')
    expect(screen.getByText(/ne comptent pas comme des échecs/)).toBeInTheDocument()
  })
})
