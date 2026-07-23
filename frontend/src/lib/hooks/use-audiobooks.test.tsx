import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import {
  chapterPollInterval,
  useAudiobook,
  useAudiobookFigures,
} from './use-audiobooks'

vi.mock('@/lib/api/audiobooks', () => ({
  audiobooksApi: {
    list: vi.fn(),
    get: vi.fn(),
    listFigures: vi.fn(),
  },
}))

import { audiobooksApi } from '@/lib/api/audiobooks'

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

describe('useAudiobook', () => {
  it('does not fetch when no id is selected', () => {
    renderHook(() => useAudiobook(null), { wrapper })
    expect(audiobooksApi.get).not.toHaveBeenCalled()
  })

  it('fetches the detail for a selected audiobook', async () => {
    vi.mocked(audiobooksApi.get).mockResolvedValue({
      id: 'audiobook:a',
      name: 'Book',
      source_id: null,
      briefing: null,
      chapter_count: 1,
      chapters: [
        {
          id: 'episode:e1',
          name: '第1章',
          chapter_index: 0,
          chapter_title: '序',
          audio_file: 'episodes/e1/a.mp3',
        },
      ],
    })
    const { result } = renderHook(() => useAudiobook('audiobook:a'), { wrapper })
    await waitFor(() => expect(result.current.data).toBeDefined())
    expect(result.current.data?.chapters[0].id).toBe('episode:e1')
  })

})

describe('chapterPollInterval', () => {
  const chapter = (audio: string | null) => ({ audio_file: audio })

  it('polls every 15s while any chapter lacks audio', () => {
    expect(
      chapterPollInterval({ chapters: [chapter('a.mp3'), chapter(null)] })
    ).toBe(15_000)
  })

  it('stops polling when every chapter has audio', () => {
    expect(
      chapterPollInterval({ chapters: [chapter('a.mp3'), chapter('b.mp3')] })
    ).toBe(false)
  })

  it('does not poll before data arrives or for empty chapter lists', () => {
    expect(chapterPollInterval(undefined)).toBe(false)
    expect(chapterPollInterval({ chapters: [] })).toBe(false)
  })
})

describe('useAudiobookFigures', () => {
  it('is gated on the audiobook id', () => {
    renderHook(() => useAudiobookFigures(null), { wrapper })
    expect(audiobooksApi.listFigures).not.toHaveBeenCalled()
  })

  it('fetches figures for the audiobook', async () => {
    vi.mocked(audiobooksApi.listFigures).mockResolvedValue([
      { id: 'book_figure:f1', page: 3, chapter_index: 0, kind: 'figure', caption: '図' },
    ])
    const { result } = renderHook(() => useAudiobookFigures('audiobook:a'), {
      wrapper,
    })
    await waitFor(() => expect(result.current.data).toBeDefined())
    expect(result.current.data?.[0].caption).toBe('図')
  })
})
