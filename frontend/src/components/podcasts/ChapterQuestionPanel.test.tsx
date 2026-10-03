import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'

import { podcastsApi } from '@/lib/api/podcasts'
import { ChapterQuestionPanel } from './ChapterQuestionPanel'

vi.mock('@/lib/api/podcasts', () => ({ podcastsApi: { askChapter: vi.fn() } }))

function panel(id = 'episode:a') {
  return <QueryClientProvider client={new QueryClient()}>
    <ChapterQuestionPanel key={id} episodeId={id} chapterName={id} />
  </QueryClientProvider>
}

beforeEach(() => {
  vi.mocked(podcastsApi.askChapter).mockReset()
})

it('keeps a failed question available for explicit retry', async () => {
  vi.mocked(podcastsApi.askChapter).mockRejectedValue(new Error('offline'))
  render(panel())
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Why?' } })
  fireEvent.click(screen.getByRole('button', { name: 'mentor.send' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('podcasts.chapterQuestionFailed')
  expect(screen.getByRole('textbox')).toHaveValue('Why?')
  expect(podcastsApi.askChapter).toHaveBeenCalledTimes(1)
})

it('shows an explicit no-evidence message', async () => {
  vi.mocked(podcastsApi.askChapter).mockResolvedValue({
    episode_id: 'episode:a', chapter_title: 'A', answer: '', excerpts: [], supported: false,
  })
  render(panel())
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'What does another book say?' } })
  fireEvent.click(screen.getByRole('button', { name: 'mentor.send' }))
  expect(await screen.findByText('podcasts.chapterQuestionNoEvidence')).toBeInTheDocument()
})

it('uses prior turns for follow-up questions', async () => {
  vi.mocked(podcastsApi.askChapter).mockResolvedValue({
    episode_id: 'episode:a', chapter_title: 'A', answer: 'First answer', excerpts: ['Evidence from the chapter.'], supported: true,
  })
  render(panel())
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Explain.' } })
  fireEvent.click(screen.getByRole('button', { name: 'mentor.send' }))
  await screen.findByText('First answer')
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Why?' } })
  fireEvent.click(screen.getByRole('button', { name: 'mentor.send' }))
  await waitFor(() => expect(podcastsApi.askChapter).toHaveBeenLastCalledWith('episode:a', 'Why?', [
    { role: 'user', content: 'Explain.' }, { role: 'assistant', content: 'First answer' },
  ], expect.any(AbortSignal)))
})

it('cancels the old chapter request when the chapter changes', async () => {
  vi.mocked(podcastsApi.askChapter).mockImplementation(() => new Promise(() => {}))
  const { rerender } = render(panel())
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Explain.' } })
  fireEvent.click(screen.getByRole('button', { name: 'mentor.send' }))
  await waitFor(() => expect(podcastsApi.askChapter).toHaveBeenCalledTimes(1))
  const signal = vi.mocked(podcastsApi.askChapter).mock.calls[0][3]
  rerender(panel('episode:b'))
  expect(signal.aborted).toBe(true)
  expect(screen.getByRole('textbox')).toHaveValue('')
})
