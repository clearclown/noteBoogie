import { useCallback, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  mentorApi,
  MentorMessage,
  MentorSourceRef,
  MentorWeightUpdate,
} from '@/lib/api/mentor'
import { QUERY_KEYS } from '@/lib/api/query-client'
import { useToast } from '@/lib/hooks/use-toast'
import { useTranslation } from '@/lib/hooks/use-translation'

// 送信中/失敗のローカル状態も持つ表示用メッセージ
export interface DisplayMessage extends Omit<MentorMessage, 'id'> {
  id: string
  pending?: boolean
  failed?: boolean
  sourceRefs?: MentorSourceRef[]
}

export function useMentorMessages() {
  return useQuery({
    queryKey: QUERY_KEYS.mentorMessages,
    queryFn: () => mentorApi.getMessages(),
  })
}

export function useMentorMemories() {
  return useQuery({
    queryKey: QUERY_KEYS.mentorMemories,
    queryFn: () => mentorApi.getMemories(),
  })
}

export function useDeleteMemory() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: mentorApi.deleteMemory,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.mentorMemories })
    },
    onError: () => {
      toast({ title: t('mentor.memoryDeleteError'), variant: 'destructive' })
    },
  })
}

let optimisticCounter = 0

/**
 * 会話の送信。楽観的に user メッセージを追加し、成功時に mentor 回答を
 * キャッシュへ追記する（サーバーの生ログと同じ形）。失敗時は user 行に
 * failed フラグを立てて再送できるようにする。
 */
export function useConsultMentor() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: mentorApi.consult,
    onMutate: async (message: string) => {
      await queryClient.cancelQueries({ queryKey: QUERY_KEYS.mentorMessages })
      const optimisticId = `optimistic:${++optimisticCounter}`
      queryClient.setQueryData<DisplayMessage[]>(
        QUERY_KEYS.mentorMessages,
        (old) => [
          ...(old ?? []),
          {
            id: optimisticId,
            role: 'user',
            content: message,
            sources: null,
            created: null,
            pending: true,
          },
        ]
      )
      return { optimisticId }
    },
    onSuccess: (response, _message, context) => {
      queryClient.setQueryData<DisplayMessage[]>(
        QUERY_KEYS.mentorMessages,
        (old) => [
          ...(old ?? []).map((m) =>
            m.id === context?.optimisticId ? { ...m, pending: false } : m
          ),
          {
            id: response.message_id ?? `optimistic:${++optimisticCounter}`,
            role: 'mentor' as const,
            content: response.answer,
            sources: response.sources.map((s) => s.id),
            sourceRefs: response.sources,
            created: null,
          },
        ]
      )
      // 記憶（memorize ノード）と傾斜の自動係数が更新される
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.mentorMemories })
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.mentorWeights })
    },
    onError: (_error, _message, context) => {
      queryClient.setQueryData<DisplayMessage[]>(
        QUERY_KEYS.mentorMessages,
        (old) =>
          (old ?? []).map((m) =>
            m.id === context?.optimisticId
              ? { ...m, pending: false, failed: true }
              : m
          )
      )
      toast({ title: t('mentor.consultError'), variant: 'destructive' })
    },
  })
}

export function useMentorWeights() {
  return useQuery({
    queryKey: QUERY_KEYS.mentorWeights,
    queryFn: mentorApi.getWeights,
  })
}

export function useUpdateWeight() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: ({
      sourceId,
      update,
    }: {
      sourceId: string
      update: MentorWeightUpdate
    }) => mentorApi.updateWeight(sourceId, update),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.mentorWeights })
      toast({ title: t('mentor.weightSaved') })
    },
    onError: () => {
      toast({ title: t('mentor.weightSaveError'), variant: 'destructive' })
    },
  })
}

/**
 * 師匠回答の音声再生。mp3 blob を取得して <audio> なしの Audio で再生する。
 * 同じメッセージはサーバー側キャッシュが効く。
 */
export function useSpeakMessage() {
  const { toast } = useToast()
  const { t } = useTranslation()
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const urlRef = useRef<string | null>(null)
  const [speakingId, setSpeakingId] = useState<string | null>(null)
  const [loadingId, setLoadingId] = useState<string | null>(null)

  const stop = useCallback(() => {
    audioRef.current?.pause()
    audioRef.current = null
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current)
      urlRef.current = null
    }
    setSpeakingId(null)
  }, [])

  const speak = useCallback(
    async (messageId: string) => {
      if (speakingId === messageId) {
        stop()
        return
      }
      stop()
      setLoadingId(messageId)
      try {
        const blob = await mentorApi.speak(messageId)
        const url = URL.createObjectURL(blob)
        const audio = new Audio(url)
        audioRef.current = audio
        urlRef.current = url
        audio.onended = stop
        audio.onerror = stop
        setSpeakingId(messageId)
        await audio.play()
      } catch {
        stop()
        toast({ title: t('mentor.speakError'), variant: 'destructive' })
      } finally {
        setLoadingId(null)
      }
    },
    [speakingId, stop, toast, t]
  )

  return { speak, stop, speakingId, loadingId }
}
