'use client'

/**
 * 5軸ルーブリックの小型SVGレーダーチャート（ライブラリ不使用）。
 * バー表示の補助として全体バランスを一目で見せる（MENTOR_UI_DESIGN §11）。
 */

const SIZE = 150
const CENTER = SIZE / 2
const RADIUS = SIZE / 2 - 24
const MAX_SCORE = 5

export interface RadarAxis {
  key: string
  label: string
  score: number
}

function point(index: number, total: number, distance: number): [number, number] {
  const angle = (Math.PI * 2 * index) / total - Math.PI / 2
  return [CENTER + distance * Math.cos(angle), CENTER + distance * Math.sin(angle)]
}

export function polygonPoints(scores: number[], radius = RADIUS): string {
  return scores
    .map((score, index) => {
      const clamped = Math.max(0, Math.min(MAX_SCORE, score))
      const [x, y] = point(index, scores.length, (clamped / MAX_SCORE) * radius)
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}

export function AxisRadar({ axes }: { axes: RadarAxis[] }) {
  if (axes.length < 3) return null
  const grid = [1, 2, 3, 4, 5]
  return (
    <svg
      width={SIZE}
      height={SIZE}
      viewBox={`0 0 ${SIZE} ${SIZE}`}
      role="img"
      aria-label={axes.map((a) => `${a.label}: ${a.score.toFixed(1)}`).join(', ')}
      data-testid="axis-radar"
      className="shrink-0"
    >
      {grid.map((level) => (
        <polygon
          key={level}
          points={polygonPoints(axes.map(() => level))}
          fill="none"
          className="stroke-border"
          strokeWidth={level === MAX_SCORE ? 1 : 0.5}
        />
      ))}
      {axes.map((_, index) => {
        const [x, y] = point(index, axes.length, RADIUS)
        return (
          <line
            key={index}
            x1={CENTER}
            y1={CENTER}
            x2={x}
            y2={y}
            className="stroke-border"
            strokeWidth={0.5}
          />
        )
      })}
      <polygon
        points={polygonPoints(axes.map((a) => a.score))}
        className="fill-primary/25 stroke-primary"
        strokeWidth={1.5}
        data-testid="axis-radar-scores"
      />
      {axes.map((axis, index) => {
        const [x, y] = point(index, axes.length, RADIUS + 13)
        return (
          <text
            key={axis.key}
            x={x}
            y={y}
            textAnchor="middle"
            dominantBaseline="middle"
            className="fill-muted-foreground"
            fontSize={8}
          >
            {axis.label}
          </text>
        )
      })}
    </svg>
  )
}
