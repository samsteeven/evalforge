import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Trajectory } from './Trajectory'

describe('Trajectory', () => {
  it("montre les outils dans l'ordre et signale un appel raté", () => {
    render(
      <Trajectory
        steps={[
          { step: 0, tool: 'escalate_to_human', arguments: { reason: 'x' }, status: 'success' },
          { step: 1, tool: 'find_order', arguments: '{"order_id": "1042"}', status: 'invalid_arguments', error: 'motif CMD-0000' },
        ]}
        stopReason="max_steps"
      />,
    )
    const items = within(screen.getByRole('list', { name: "Trajectoire de l'agent" })).getAllByRole('listitem')
    expect(items.map((item) => item.textContent)).toEqual([
      'escalate_to_human',
      '→find_order (arguments invalides)',
      'arrêt : max_steps',
    ])
    expect(screen.getByText(/find_order/)).toHaveAttribute('title', '{"order_id": "1042"} : motif CMD-0000')
  })

  it("dit quand l'agent n'a appelé aucun outil", () => {
    render(<Trajectory steps={[]} stopReason="completed" />)
    expect(screen.getByText('aucun outil appelé')).toBeInTheDocument()
  })
})
