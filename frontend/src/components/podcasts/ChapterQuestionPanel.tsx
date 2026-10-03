'use client'

import { useState } from 'react'
import { Loader2, Send } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { MarkdownRenderer } from '@/components/ui/markdown-renderer'
import { useChapterQuestion } from '@/lib/hooks/use-chapter-question'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { ChapterAnswer, ChapterConversationMessage } from '@/lib/api/podcasts'

export function ChapterQuestionPanel({ episodeId, chapterName }: {
  episodeId: string
  chapterName: string
}) {
  const { t } = useTranslation()
  const ask = useChapterQuestion(episodeId)
  const [draft, setDraft] = useState('')
  const [turns, setTurns] = useState<{ question: string; response: ChapterAnswer }[]>([])

  const submit = async () => {
    const question = draft.trim()
    if (!question || ask.isPending) return
    const history: ChapterConversationMessage[] = turns.slice(-3).flatMap((turn) => [
      { role: 'user', content: turn.question },
      { role: 'assistant', content: turn.response.answer || t('podcasts.chapterQuestionNoEvidence') },
    ])
    try {
      const response = await ask.mutateAsync({ question, history })
      setTurns((previous) => [...previous, { question, response }])
      setDraft('')
    } catch {
      // Keep the draft for an explicit retry; the hook reports the failure.
    }
  }

  return (
    <section aria-label={t('podcasts.chapterQuestionTitle')} className="space-y-3 border-t pt-4">
      <div>
        <h3 className="font-medium">{t('podcasts.chapterQuestionTitle')}</h3>
        <p className="break-words text-sm text-muted-foreground">{chapterName}</p>
        <p className="text-xs text-muted-foreground">{t('podcasts.chapterQuestionScope')}</p>
      </div>
      <div className="max-h-80 space-y-4 overflow-y-auto" aria-live="polite" aria-relevant="additions">
        {turns.map((turn, index) => (
          <div key={index} className="space-y-2 text-sm">
            <p className="whitespace-pre-wrap break-words font-medium">{turn.question}</p>
            {turn.response.supported ? (
              <>
                <MarkdownRenderer>{turn.response.answer}</MarkdownRenderer>
                <details>
                  <summary className="cursor-pointer text-muted-foreground">{t('podcasts.chapterQuestionEvidence')}</summary>
                  {turn.response.excerpts.map((quote, i) => (
                    <blockquote key={i} className="mt-2 break-words border-l-2 pl-3 text-muted-foreground">{quote}</blockquote>
                  ))}
                </details>
              </>
            ) : <p>{t('podcasts.chapterQuestionNoEvidence')}</p>}
          </div>
        ))}
      </div>
      <form className="space-y-2" onSubmit={(event) => { event.preventDefault(); void submit() }}>
        <Textarea
          autoFocus
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          aria-label={t('podcasts.chapterQuestionPlaceholder')}
          placeholder={t('podcasts.chapterQuestionPlaceholder')}
          maxLength={2000}
          disabled={ask.isPending}
          rows={2}
        />
        {ask.isError && <p role="alert" className="text-sm text-destructive">{t('podcasts.chapterQuestionFailed')}</p>}
        <Button type="submit" className="min-h-11 w-full" disabled={!draft.trim() || ask.isPending}>
          {ask.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          {ask.isPending ? t('podcasts.chapterQuestionThinking') : t('mentor.send')}
        </Button>
      </form>
    </section>
  )
}
