import { useQuery } from '@tanstack/react-query'

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

export function useAudiobook(audiobookId: string | null) {
  return useQuery({
    queryKey: AUDIOBOOK_QUERY_KEYS.audiobook(audiobookId ?? ''),
    queryFn: () => audiobooksApi.get(audiobookId as string),
    enabled: Boolean(audiobookId),
    refetchInterval: (query) => {
      // Poll while any chapter is still missing audio.
      const detail = query.state.data
      const pending = detail?.chapters?.some((c) => !c.audio_file)
      return pending ? 15_000 : false
    },
  })
}

export function useAudiobookFigures(audiobookId: string | null) {
  return useQuery({
    queryKey: AUDIOBOOK_QUERY_KEYS.audiobookFigures(audiobookId ?? ''),
    queryFn: () => audiobooksApi.listFigures(audiobookId as string),
    enabled: Boolean(audiobookId),
  })
}
