'use client'

import { useEffect, useRef } from 'react'
import { useMutation } from '@tanstack/react-query'
import { toast } from 'sonner'

import { podcastsApi, type ChapterConversationMessage } from '@/lib/api/podcasts'
import { useTranslation } from './use-translation'

export function useChapterQuestion(episodeId: string) {
  const { t } = useTranslation()
  const controller = useRef<AbortController | null>(null)
  useEffect(() => () => controller.current?.abort(), [episodeId])

  return useMutation({
    mutationFn: ({ question, history }: { question: string; history: ChapterConversationMessage[] }) => {
      controller.current?.abort()
      controller.current = new AbortController()
      return podcastsApi.askChapter(episodeId, question, history, controller.current.signal)
    },
    retry: false,
    onError: () => {
      if (!controller.current?.signal.aborted) toast.error(t('podcasts.chapterQuestionFailed'))
    },
  })
}
