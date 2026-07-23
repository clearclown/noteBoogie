'use client'

import { useRef, useState } from 'react'
import {
  BookOpen,
  CheckCircle2,
  Download,
  FileUp,
  Loader2,
  XCircle,
} from 'lucide-react'

import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Progress } from '@/components/ui/progress'
import {
  useApplySlideFixes,
  useReviewSlides,
  useSlideReviews,
} from '@/lib/hooks/use-mentor'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { SlideReview } from '@/lib/api/mentor'

const ACCEPT = '.png,.jpg,.jpeg,.pdf,.pptx'

// 軸キー→i18nキーの静的対応（リテラルにしておくと未使用キー検出が効く）
const AXIS_LABEL_KEYS: Record<string, string> = {
  logic: 'mentor.axis_logic',
  message_body: 'mentor.axis_message_body',
  charts: 'mentor.axis_charts',
  tone_manner: 'mentor.axis_tone_manner',
  design: 'mentor.axis_design',
}

function GateBadge({ review }: { review: SlideReview }) {
  const { t } = useTranslation()
  return review.passed ? (
    <Badge className="gap-1 bg-emerald-600 hover:bg-emerald-600">
      <CheckCircle2 className="h-3.5 w-3.5" />
      {t('mentor.gatePassed')}
    </Badge>
  ) : (
    <Badge variant="destructive" className="gap-1">
      <XCircle className="h-3.5 w-3.5" />
      {t('mentor.gateFailed', { threshold: review.threshold.toFixed(1) })}
    </Badge>
  )
}

function ReviewResult({ review }: { review: SlideReview }) {
  const { t } = useTranslation()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const applyFixes = useApplySlideFixes()

  const axisLabel = (key: string) => t(AXIS_LABEL_KEYS[key] ?? key)
  const applicableCount = review.axes
    .flatMap((a) => a.issues)
    .filter((i) => i.applicable && i.id).length

  const toggle = (issueId: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(issueId)) next.delete(issueId)
      else next.add(issueId)
      return next
    })
  }

  return (
    <div className="space-y-4 rounded-lg border p-4" data-testid="slide-review-result">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{review.filename}</span>
        <span className="text-xs text-muted-foreground">
          {review.page_count}p ・ {review.kind}
        </span>
        <GateBadge review={review} />
        <span className="ml-auto text-2xl font-semibold tabular-nums">
          {review.overall.toFixed(1)}
          <span className="text-sm font-normal text-muted-foreground"> / 5</span>
        </span>
      </div>

      {review.summary && <p className="text-sm text-muted-foreground">{review.summary}</p>}
      {review.top_fix && !review.passed && (
        <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm">
          <span className="font-medium">{t('mentor.topFix')}: </span>
          {review.top_fix}
        </p>
      )}

      <ul className="space-y-3">
        {review.axes.map((axis) => (
          <li key={axis.key} data-testid={`axis-${axis.key}`}>
            <div className="mb-1 flex items-center gap-2 text-sm">
              <span className={cn('w-44 shrink-0', !axis.passed && 'text-destructive')}>
                {axisLabel(axis.key)}
              </span>
              <Progress value={axis.score * 20} className="h-2 flex-1" />
              <span className="w-8 text-right text-xs tabular-nums">
                {axis.score.toFixed(1)}
              </span>
            </div>
            {axis.issues.length > 0 && (
              <ul className="ml-4 space-y-1.5">
                {axis.issues.map((issue, index) => (
                  <li key={issue.id ?? index} className="flex items-start gap-2 text-xs">
                    {issue.applicable && issue.id ? (
                      <Checkbox
                        className="mt-0.5"
                        checked={selected.has(issue.id)}
                        onCheckedChange={() => toggle(issue.id!)}
                        aria-label={t('mentor.applySelect')}
                      />
                    ) : (
                      <span className="mt-1 h-1 w-1 shrink-0 rounded-full bg-muted-foreground" />
                    )}
                    <span>
                      <span className="text-muted-foreground">p{issue.page}: </span>
                      {issue.text}
                      {issue.fix && (
                        <span className="block text-emerald-700 dark:text-emerald-400">
                          → {issue.fix}
                        </span>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>

      <div className="flex flex-wrap items-center gap-2">
        {review.citations.map((citation) => (
          <Badge key={citation.id} variant="secondary" className="gap-1 font-normal">
            <BookOpen className="h-3 w-3" />
            {citation.title}
          </Badge>
        ))}
        {review.kind === 'pptx' && applicableCount > 0 && review.id && (
          <Button
            size="sm"
            className="ml-auto gap-1.5"
            disabled={selected.size === 0 || applyFixes.isPending}
            onClick={() =>
              applyFixes.mutate({
                reviewId: review.id!,
                issueIds: [...selected],
                filename: review.filename,
              })
            }
          >
            {applyFixes.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Download className="h-4 w-4" />
            )}
            {t('mentor.applyFixes', { count: selected.size })}
          </Button>
        )}
      </div>
    </div>
  )
}

export function MentorSlidesTab() {
  const { t } = useTranslation()
  const inputRef = useRef<HTMLInputElement | null>(null)
  const review = useReviewSlides()
  const { data: history } = useSlideReviews()

  const latest = review.data ?? history?.[0]
  const past = (history ?? []).filter((r) => r.id !== latest?.id)

  return (
    <div className="space-y-4">
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={review.isPending}
        className="flex w-full flex-col items-center gap-2 rounded-lg border-2 border-dashed p-8 text-sm text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground"
        data-testid="slide-dropzone"
      >
        {review.isPending ? (
          <>
            <Loader2 className="h-6 w-6 animate-spin" />
            {t('mentor.slideReviewing')}
          </>
        ) : (
          <>
            <FileUp className="h-6 w-6" />
            {t('mentor.slideDropzone')}
          </>
        )}
      </button>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        className="hidden"
        data-testid="slide-file-input"
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) review.mutate(file)
          e.target.value = ''
        }}
      />

      {latest && <ReviewResult review={latest} />}

      {past.length > 0 && (
        <div className="space-y-2">
          <p className="text-sm font-medium">{t('mentor.slideHistory')}</p>
          {past.map((item) => (
            <ReviewResult key={item.id} review={item} />
          ))}
        </div>
      )}
    </div>
  )
}
