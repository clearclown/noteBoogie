import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { AxisRadar, polygonPoints } from './AxisRadar'

const AXES = [
  { key: 'logic', label: '論理', score: 5 },
  { key: 'message_body', label: '整合', score: 2.5 },
  { key: 'charts', label: '図表', score: 0 },
  { key: 'tone_manner', label: 'トンマナ', score: 4 },
  { key: 'design', label: 'デザイン', score: 3 },
]

describe('polygonPoints', () => {
  it('maps full score to the outer radius and zero to the center', () => {
    const full = polygonPoints([5, 5, 5], 50)
    const zero = polygonPoints([0, 0, 0], 50)
    // 最初の頂点は真上（center=75, radius方向 -y）
    expect(full.split(' ')[0]).toBe('75.0,25.0')
    expect(zero.split(' ')).toEqual(['75.0,75.0', '75.0,75.0', '75.0,75.0'])
  })

  it('clamps out-of-range scores', () => {
    expect(polygonPoints([99, -5, 5], 50)).toBe(polygonPoints([5, 0, 5], 50))
  })
})

describe('AxisRadar', () => {
  it('renders grid, score polygon and labels', () => {
    render(<AxisRadar axes={AXES} />)
    const radar = screen.getByTestId('axis-radar')
    expect(radar).toBeInTheDocument()
    expect(screen.getByTestId('axis-radar-scores')).toBeInTheDocument()
    expect(screen.getByText('トンマナ')).toBeInTheDocument()
    expect(radar).toHaveAccessibleName(expect.stringContaining('論理: 5.0'))
  })

  it('renders nothing with fewer than 3 axes', () => {
    const { container } = render(<AxisRadar axes={AXES.slice(0, 2)} />)
    expect(container.firstChild).toBeNull()
  })
})
