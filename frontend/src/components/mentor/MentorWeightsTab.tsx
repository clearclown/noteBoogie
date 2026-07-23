'use client'

import { useState } from 'react'
import { ChevronDown, ChevronRight, Loader2, TrendingUp } from 'lucide-react'

import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useMentorWeights, useUpdateWeight } from '@/lib/hooks/use-mentor'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { MentorWeightEntry } from '@/lib/api/mentor'

function WeightSlider({
  value,
  onCommit,
  disabled,
  label,
}: {
  value: number
  onCommit: (value: number) => void
  disabled?: boolean
  label: string
}) {
  const [local, setLocal] = useState<number | null>(null)
  const shown = local ?? value
  return (
    <div className="flex items-center gap-2">
      <input
        type="range"
        min={0}
        max={2}
        step={0.1}
        value={shown}
        aria-label={label}
        disabled={disabled}
        onChange={(e) => setLocal(Number(e.target.value))}
        onMouseUp={() => {
          if (local !== null && local !== value) onCommit(local)
          setLocal(null)
        }}
        onTouchEnd={() => {
          if (local !== null && local !== value) onCommit(local)
          setLocal(null)
        }}
        onKeyUp={(e) => {
          if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && local !== null) {
            onCommit(local)
            setLocal(null)
          }
        }}
        className="h-1.5 w-36 cursor-pointer accent-primary"
      />
      <span
        className={cn(
          'w-8 text-right text-xs tabular-nums',
          shown === 0 ? 'text-destructive' : 'text-muted-foreground'
        )}
      >
        {shown.toFixed(1)}
      </span>
    </div>
  )
}

function WeightRow({ entry }: { entry: MentorWeightEntry }) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const updateWeight = useUpdateWeight()

  const commitBookWeight = (weight: number) => {
    updateWeight.mutate({
      sourceId: entry.source_id,
      update: { weight, chapter_weights: entry.chapter_weights },
    })
  }

  const commitChapterWeight = (chapterIndex: number, weight: number) => {
    const chapterWeights = { ...(entry.chapter_weights ?? {}) }
    if (weight === 1.0) {
      delete chapterWeights[String(chapterIndex)]
    } else {
      chapterWeights[String(chapterIndex)] = weight
    }
    updateWeight.mutate({
      sourceId: entry.source_id,
      update: { weight: entry.weight, chapter_weights: chapterWeights },
    })
  }

  return (
    <li className="rounded-lg border" data-testid="mentor-weight-row">
      <div className="flex items-center gap-3 px-4 py-3">
        <Button
          variant="ghost"
          size="sm"
          className="h-7 w-7 shrink-0 p-0"
          onClick={() => setExpanded((v) => !v)}
          disabled={entry.chapters.length === 0}
          aria-label={t('mentor.weightChapters')}
        >
          {expanded ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </Button>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{entry.title}</p>
          {entry.weight === 0 && (
            <p className="text-xs text-destructive">{t('mentor.weightExcluded')}</p>
          )}
        </div>
        {entry.auto_factor > 1.0 && (
          <Badge variant="secondary" className="gap-1 font-normal" title={t('mentor.weightAutoHint')}>
            <TrendingUp className="h-3 w-3" />
            ×{entry.auto_factor.toFixed(2)}
          </Badge>
        )}
        {updateWeight.isPending && (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
        )}
        <WeightSlider
          value={entry.weight}
          onCommit={commitBookWeight}
          disabled={updateWeight.isPending}
          label={`${t('mentor.weightBook')}: ${entry.title}`}
        />
      </div>
      {expanded && entry.chapters.length > 0 && (
        <ul className="space-y-2 border-t bg-muted/30 px-4 py-3 pl-14">
          {entry.chapters.map((chapter, index) => (
            <li key={index} className="flex items-center justify-between gap-3">
              <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                {chapter || `#${index + 1}`}
              </span>
              <WeightSlider
                value={entry.chapter_weights?.[String(index)] ?? 1.0}
                onCommit={(w) => commitChapterWeight(index, w)}
                disabled={updateWeight.isPending}
                label={`${t('mentor.weightChapter')}: ${chapter || index + 1}`}
              />
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

export function MentorWeightsTab() {
  const { t } = useTranslation()
  const { data: entries, isLoading } = useMentorWeights()

  if (isLoading) {
    return (
      <div className="flex justify-center p-12">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (!entries || entries.length === 0) {
    return (
      <p className="p-8 text-center text-sm text-muted-foreground">
        {t('mentor.weightsEmpty')}
      </p>
    )
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">{t('mentor.weightsHelp')}</p>
      <ul className="space-y-2">
        {entries.map((entry) => (
          <WeightRow key={entry.source_id} entry={entry} />
        ))}
      </ul>
    </div>
  )
}
