import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AudiobooksTab } from './AudiobooksTab'

vi.mock('@/lib/api/audiobooks', () => ({
  audiobooksApi: {
    list: vi.fn(),
    get: vi.fn(),
    listFigures: vi.fn(),
    delete: vi.fn(),
    generate: vi.fn(),
    retryChapter: vi.fn(),
    figureImageUrl: (id: string) => `http://gw/figures/${id}/image`,
  },
}))

vi.mock('@/lib/config', () => ({
  getApiUrl: vi.fn(async () => 'http://api:5055'),
}))

vi.mock('@/lib/api/podcasts', () => ({
  podcastsApi: {
    setEpisodeFeedback: vi.fn(),
  },
}))

vi.mock('@/lib/hooks/use-podcasts', () => ({
  useEpisodeProfiles: () => ({
    episodeProfiles: [
      { id: 'ep:1', name: 'book_navigator' },
      { id: 'ep:2', name: 'book_navigator_budget' },
    ],
  }),
  useSpeakerProfiles: () => ({
    speakerProfiles: [
      { id: 'sp:1', name: 'book_navigator_mentor' },
      { id: 'sp:2', name: 'book_navigator_mentor_eco' },
    ],
  }),
}))

vi.mock('@/lib/api/sources', () => ({
  sourcesApi: {
    list: vi.fn(async () => [
      { id: 'source:s1', title: 'コンサル頭のつくり方' },
    ]),
  },
}))

import { audiobooksApi } from '@/lib/api/audiobooks'
import { podcastsApi } from '@/lib/api/podcasts'

function renderTab() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return render(<AudiobooksTab />, { wrapper })
}

const AUDIOBOOK = {
  id: 'audiobook:a',
  name: 'コンサル頭のつくり方',
  source_id: 'source:s1',
  briefing: null,
  chapter_count: 3,
}

const DETAIL = {
  ...AUDIOBOOK,
  chapters: [
    {
      id: 'episode:c0',
      name: '第1章：序',
      chapter_index: 0,
      chapter_title: '序',
      audio_file: 'episodes/c0/a.mp3',
    },
    {
      id: 'episode:c1',
      name: '第2章：本論',
      chapter_index: 1,
      chapter_title: '本論',
      audio_file: null, // still generating -> unplayable
    },
    {
      id: 'episode:c2',
      name: '第3章：結',
      chapter_index: 2,
      chapter_title: '結',
      audio_file: 'episodes/c2/a.mp3',
    },
  ],
}

const FIGURES = [
  { id: 'book_figure:f0', page: 2, chapter_index: 0, kind: 'figure', caption: '序の図' },
  { id: 'book_figure:f2', page: 9, chapter_index: 2, kind: 'figure', caption: '結の図' },
]

beforeEach(() => {
  vi.mocked(audiobooksApi.list).mockReset()
  vi.mocked(audiobooksApi.get).mockReset()
  vi.mocked(audiobooksApi.listFigures).mockReset()
  vi.mocked(audiobooksApi.delete).mockReset()
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, blob: async () => new Blob(['audio']) }))
  )
  vi.stubGlobal('URL', {
    ...URL,
    createObjectURL: vi.fn(() => 'blob:audio'),
    revokeObjectURL: vi.fn(),
  })
  // jsdom has no audio playback engine.
  window.HTMLMediaElement.prototype.play = vi.fn(async () => {})
  window.HTMLMediaElement.prototype.pause = vi.fn()
})

describe('AudiobooksTab list view', () => {
  it('shows the empty state when there are no audiobooks', async () => {
    vi.mocked(audiobooksApi.list).mockResolvedValue([])
    renderTab()
    expect(await screen.findByText('podcasts.audiobooksEmpty')).toBeInTheDocument()
  })

  it('renders audiobook cards with chapter counts', async () => {
    vi.mocked(audiobooksApi.list).mockResolvedValue([AUDIOBOOK])
    renderTab()
    expect(await screen.findByText('コンサル頭のつくり方')).toBeInTheDocument()
    // The identity t() mock returns the key; the count interpolation target
    // lives inside the real locale strings, so assert the key rendered.
    expect(screen.getByText('podcasts.audiobookChapterCount')).toBeInTheDocument()
  })

  it('deletes an audiobook without opening it', async () => {
    vi.mocked(audiobooksApi.list).mockResolvedValue([AUDIOBOOK])
    vi.mocked(audiobooksApi.delete).mockResolvedValue(undefined)
    renderTab()
    await screen.findByText('コンサル頭のつくり方')
    fireEvent.click(screen.getByLabelText('common.delete'))
    await waitFor(() =>
      expect(audiobooksApi.delete).toHaveBeenCalledWith('audiobook:a')
    )
    // Still on the list view (no detail heading appeared).
    expect(vi.mocked(audiobooksApi.get)).not.toHaveBeenCalled()
  })
})

describe('AudiobooksTab detail view', () => {
  async function openDetail() {
    vi.mocked(audiobooksApi.list).mockResolvedValue([AUDIOBOOK])
    vi.mocked(audiobooksApi.get).mockResolvedValue(DETAIL)
    vi.mocked(audiobooksApi.listFigures).mockResolvedValue(FIGURES)
    const utils = renderTab()
    fireEvent.click(await screen.findByText('コンサル頭のつくり方'))
    await screen.findByText('第1章：序')
    return utils
  }

  it('renders the tracklist with pending chapters disabled', async () => {
    await openDetail()
    expect(screen.getByText('第2章：本論').closest('button')).toBeDisabled()
    expect(screen.getByText('第1章：序').closest('button')).toBeEnabled()
    expect(screen.getByText('podcasts.audiobookAudioPending')).toBeInTheDocument()
  })

  it('loads chapter audio through the protected API when a track is chosen', async () => {
    await openDetail()
    fireEvent.click(screen.getByText('第1章：序'))
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        'http://api:5055/api/podcasts/episodes/episode%3Ac0/audio',
        expect.anything()
      )
    )
  })

  it('auto-advances past unplayable chapters when a track ends', async () => {
    const { container } = await openDetail()
    fireEvent.click(screen.getByText('第1章：序'))
    await waitFor(() => expect(container.querySelector('audio')).not.toBeNull())

    fireEvent.ended(container.querySelector('audio') as HTMLAudioElement)
    // Chapter 2 (no audio) is skipped; chapter 3 loads.
    await waitFor(() =>
      expect(fetch).toHaveBeenLastCalledWith(
        'http://api:5055/api/podcasts/episodes/episode%3Ac2/audio',
        expect.anything()
      )
    )
  })

  it('does not advance when auto-advance is unchecked', async () => {
    const { container } = await openDetail()
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByText('第1章：序'))
    await waitFor(() => expect(container.querySelector('audio')).not.toBeNull())
    const calls = vi.mocked(fetch).mock.calls.length

    fireEvent.ended(container.querySelector('audio') as HTMLAudioElement)
    await new Promise((r) => setTimeout(r, 20))
    expect(vi.mocked(fetch).mock.calls.length).toBe(calls)
  })

  it('filters the figure gallery to the playing chapter, falling back to all', async () => {
    await openDetail()
    // No chapter selected -> all figures shown.
    expect(screen.getByText('序の図')).toBeInTheDocument()
    expect(screen.getByText('結の図')).toBeInTheDocument()

    fireEvent.click(screen.getByText('第1章：序'))
    await waitFor(() => {
      expect(screen.getByText('序の図')).toBeInTheDocument()
      expect(screen.queryByText('結の図')).not.toBeInTheDocument()
    })
  })

  it('shows the audio-unavailable message when the blob fetch fails', async () => {
    vi.mocked(audiobooksApi.list).mockResolvedValue([AUDIOBOOK])
    vi.mocked(audiobooksApi.get).mockResolvedValue(DETAIL)
    vi.mocked(audiobooksApi.listFigures).mockResolvedValue([])
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 403 })))
    renderTab()
    fireEvent.click(await screen.findByText('コンサル頭のつくり方'))
    fireEvent.click(await screen.findByText('第1章：序'))
    expect(await screen.findByText('podcasts.audioUnavailable')).toBeInTheDocument()
  })

  it('renders no gallery card when the book has no figures', async () => {
    vi.mocked(audiobooksApi.list).mockResolvedValue([AUDIOBOOK])
    vi.mocked(audiobooksApi.get).mockResolvedValue(DETAIL)
    vi.mocked(audiobooksApi.listFigures).mockResolvedValue([])
    renderTab()
    fireEvent.click(await screen.findByText('コンサル頭のつくり方'))
    await screen.findByText('第1章：序')
    expect(screen.queryByText('podcasts.audiobookFigures')).not.toBeInTheDocument()
  })

  it('keeps the card visible when deletion fails', async () => {
    vi.mocked(audiobooksApi.list).mockResolvedValue([AUDIOBOOK])
    vi.mocked(audiobooksApi.delete).mockRejectedValue(new Error('gateway down'))
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    renderTab()
    await screen.findByText('コンサル頭のつくり方')
    fireEvent.click(screen.getByLabelText('common.delete'))
    await waitFor(() => expect(audiobooksApi.delete).toHaveBeenCalled())
    expect(screen.getByText('コンサル頭のつくり方')).toBeInTheDocument()
    expect(errorSpy).toHaveBeenCalled()
    errorSpy.mockRestore()
  })

  it('falls back to unauthenticated audio fetch when auth storage is corrupt', async () => {
    window.localStorage.setItem('auth-storage', '{not json')
    vi.mocked(audiobooksApi.list).mockResolvedValue([AUDIOBOOK])
    vi.mocked(audiobooksApi.get).mockResolvedValue(DETAIL)
    vi.mocked(audiobooksApi.listFigures).mockResolvedValue([])
    renderTab()
    fireEvent.click(await screen.findByText('コンサル頭のつくり方'))
    fireEvent.click(await screen.findByText('第1章：序'))
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    const headers = vi.mocked(fetch).mock.calls[0][1]?.headers as Record<string, string>
    expect(headers?.Authorization).toBeUndefined()
    window.localStorage.removeItem('auth-storage')
  })

  it('marks a failed chapter with a destructive badge, not generating', async () => {
    vi.mocked(audiobooksApi.list).mockResolvedValue([AUDIOBOOK])
    vi.mocked(audiobooksApi.get).mockResolvedValue({
      ...DETAIL,
      chapters: [
        { ...DETAIL.chapters[0] },
        {
          ...DETAIL.chapters[1],
          generation_error: 'sidecar exploded',
        },
      ],
    })
    vi.mocked(audiobooksApi.listFigures).mockResolvedValue([])
    renderTab()
    fireEvent.click(await screen.findByText('コンサル頭のつくり方'))
    await screen.findByText('第1章：序')
    expect(screen.getByText('podcasts.audiobookFailed')).toBeInTheDocument()
    expect(screen.getByText('podcasts.audiobookFailed')).toHaveAttribute(
      'title',
      'sidecar exploded'
    )
    expect(screen.queryByText('podcasts.audiobookAudioPending')).not.toBeInTheDocument()
  })

  it('starts generation from the dialog with a selected source', async () => {
    vi.mocked(audiobooksApi.list).mockResolvedValue([])
    vi.mocked(audiobooksApi.generate).mockResolvedValue({
      audiobook_id: 'audiobook:new',
      audiobook_name: '本',
      chapter_count: 8,
      status: 'processing',
    })
    renderTab()
    fireEvent.click(await screen.findByText('podcasts.audiobookGenerate'))
    await screen.findByText('podcasts.audiobookGenerateTitle')

    const [sourceSelect, profileSelect, voiceSelect] = screen.getAllByRole(
      'combobox'
    ) as HTMLSelectElement[]
    await waitFor(() =>
      expect(sourceSelect.querySelectorAll('option').length).toBeGreaterThan(1)
    )
    fireEvent.change(sourceSelect, { target: { value: 'source:s1' } })
    // Name auto-fills from the source title.
    const nameInput = screen.getByRole('textbox') as HTMLInputElement
    expect(nameInput.value).toBe('コンサル頭のつくり方')

    // Cost/quality is the user's choice: pick the budget script + eco voice.
    fireEvent.change(profileSelect, {
      target: { value: 'book_navigator_budget' },
    })
    fireEvent.change(voiceSelect, {
      target: { value: 'book_navigator_mentor_eco' },
    })

    fireEvent.click(screen.getByText('podcasts.audiobookGenerateStart'))
    await waitFor(() =>
      expect(audiobooksApi.generate).toHaveBeenCalledWith({
        audiobook_name: 'コンサル頭のつくり方',
        source_id: 'source:s1',
        episode_profile: 'book_navigator_budget',
        speaker_profile: 'book_navigator_mentor_eco',
      })
    )
  })

  it('retries a failed chapter from the tracklist', async () => {
    vi.mocked(audiobooksApi.list).mockResolvedValue([AUDIOBOOK])
    vi.mocked(audiobooksApi.get).mockResolvedValue({
      ...DETAIL,
      chapters: [
        { ...DETAIL.chapters[1], generation_error: 'outline parse failure' },
      ],
    })
    vi.mocked(audiobooksApi.listFigures).mockResolvedValue([])
    vi.mocked(audiobooksApi.retryChapter).mockResolvedValue(undefined)
    renderTab()
    fireEvent.click(await screen.findByText('コンサル頭のつくり方'))
    fireEvent.click(await screen.findByText('podcasts.audiobookRetry'))
    await waitFor(() =>
      expect(audiobooksApi.retryChapter).toHaveBeenCalledWith('episode:c1')
    )
  })

  it('returns to the list with the back button', async () => {
    await openDetail()
    const backButton = screen
      .getAllByRole('button')
      .find((b) => b.querySelector('svg.lucide-arrow-left'))
    fireEvent.click(backButton as HTMLElement)
    await screen.findByText('コンサル頭のつくり方')
  })

  it('sends thumbs feedback for a completed chapter and toggles it off', async () => {
    vi.mocked(podcastsApi.setEpisodeFeedback).mockResolvedValue({
      id: 'episode:c0',
      feedback: 'up',
    })
    vi.mocked(audiobooksApi.get).mockResolvedValue({
      ...DETAIL,
      chapters: [
        { ...DETAIL.chapters[0], feedback: null },
        DETAIL.chapters[1],
        { ...DETAIL.chapters[2], feedback: 'down' },
      ],
    })
    vi.mocked(audiobooksApi.list).mockResolvedValue([AUDIOBOOK])
    vi.mocked(audiobooksApi.listFigures).mockResolvedValue([])
    renderTab()
    fireEvent.click(await screen.findByText('コンサル頭のつくり方'))
    await screen.findByText('第1章：序')

    // 完了章（2章）×2ボタン。生成中の章にはボタンが無い
    const upButtons = screen.getAllByRole('button', {
      name: 'podcasts.chapterFeedbackUp',
    })
    expect(upButtons).toHaveLength(2)

    fireEvent.click(upButtons[0])
    await waitFor(() =>
      expect(podcastsApi.setEpisodeFeedback).toHaveBeenCalledWith('episode:c0', 'up')
    )

    // 既に down が付いている章の down を押すと取り消し（null）
    const downButtons = screen.getAllByRole('button', {
      name: 'podcasts.chapterFeedbackDown',
    })
    fireEvent.click(downButtons[1])
    await waitFor(() =>
      expect(podcastsApi.setEpisodeFeedback).toHaveBeenCalledWith('episode:c2', null)
    )
  })
})
