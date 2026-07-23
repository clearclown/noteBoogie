'use client'

import { BookOpen, GraduationCap, Loader2, RotateCcw, User, Volume2, VolumeX } from 'lucide-react'

import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { MarkdownRenderer } from '@/components/ui/markdown-renderer'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { DisplayMessage } from '@/lib/hooks/use-mentor'

interface MentorMessageProps {
  message: DisplayMessage
  onRetry?: (content: string) => void
  onSpeak?: (messageId: string) => void
  speaking?: boolean
  speakLoading?: boolean
}

export function MentorMessage({
  message,
  onRetry,
  onSpeak,
  speaking = false,
  speakLoading = false,
}: MentorMessageProps) {
  const { t } = useTranslation()
  const isMentor = message.role === 'mentor'
  // speak はサーバー永続 id が付いた回答のみ（楽観行はTTS不可）
  const canSpeak = isMentor && !!onSpeak && !message.id.startsWith('optimistic:')

  return (
    <div
      className={cn('flex gap-3', !isMentor && 'flex-row-reverse')}
      data-testid={`mentor-message-${message.role}`}
    >
      <div
        className={cn(
          'mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full',
          isMentor ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'
        )}
      >
        {isMentor ? <GraduationCap className="h-4 w-4" /> : <User className="h-4 w-4" />}
      </div>
      <div className={cn('max-w-[80%] space-y-2', !isMentor && 'text-right')}>
        <div
          className={cn(
            'rounded-lg px-4 py-3 text-left text-sm',
            isMentor ? 'bg-muted/50' : 'bg-primary text-primary-foreground'
          )}
        >
          {isMentor ? (
            <MarkdownRenderer>{message.content}</MarkdownRenderer>
          ) : (
            <p className="whitespace-pre-wrap">{message.content}</p>
          )}
        </div>
        <div className={cn('flex flex-wrap items-center gap-2', !isMentor && 'justify-end')}>
          {message.pending && (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
          )}
          {message.failed && onRetry && (
            <Button
              variant="outline"
              size="sm"
              className="h-7 gap-1 text-destructive"
              onClick={() => onRetry(message.content)}
            >
              <RotateCcw className="h-3 w-3" />
              {t('mentor.retry')}
            </Button>
          )}
          {canSpeak && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1 text-muted-foreground"
              aria-label={t('mentor.speak')}
              onClick={() => onSpeak(message.id)}
              disabled={speakLoading}
            >
              {speakLoading ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : speaking ? (
                <VolumeX className="h-3.5 w-3.5" />
              ) : (
                <Volume2 className="h-3.5 w-3.5" />
              )}
            </Button>
          )}
          {isMentor &&
            (message.sourceRefs ?? []).map((ref) => (
              <Badge key={ref.id} variant="secondary" className="gap-1 font-normal">
                <BookOpen className="h-3 w-3" />
                {ref.title}
              </Badge>
            ))}
        </div>
      </div>
    </div>
  )
}
