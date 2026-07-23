import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { audiobooksApi } from '@/lib/api/audiobooks'

export const AUDIOBOOK_QUERY_KEYS = {
  audiobooks: ['audiobooks'] as const,
  audiobook: (id: string) => ['audiobooks', id] as const,
  audiobookFigures: (id: string) => ['audiobooks', id, 'figures'] as const,
}

export function useAudiobooks() {
  return useQuery({
    queryKey: AUDIOBOOK_QUERY_KEYS.audiobooks,
    queryFn: audiobooksApi.list,
    // Chapters keep completing in the background while the tab is open.
    refetchInterval: 30_000,
  })
}

/** Poll while any chapter is still missing audio; stop once all are done. */
export function chapterPollInterval(
  detail: { chapters?: { audio_file: string | null }[] } | undefined
): number | false {
  const pending = detail?.chapters?.some((c) => !c.audio_file)
  return pending ? 15_000 : false
}

export function useAudiobook(audiobookId: string | null) {
  return useQuery({
    queryKey: AUDIOBOOK_QUERY_KEYS.audiobook(audiobookId ?? ''),
    queryFn: () => audiobooksApi.get(audiobookId as string),
    enabled: Boolean(audiobookId),
    refetchInterval: (query) => chapterPollInterval(query.state.data),
  })
}

export function useAudiobookFigures(audiobookId: string | null) {
  return useQuery({
    queryKey: AUDIOBOOK_QUERY_KEYS.audiobookFigures(audiobookId ?? ''),
    queryFn: () => audiobooksApi.listFigures(audiobookId as string),
    enabled: Boolean(audiobookId),
  })
}

export function useGenerateAudiobook() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: audiobooksApi.generate,
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: AUDIOBOOK_QUERY_KEYS.audiobooks,
      })
    },
  })
}
