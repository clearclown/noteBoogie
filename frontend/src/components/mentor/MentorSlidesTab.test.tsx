import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { MentorSlidesTab } from './MentorSlidesTab'
import type { SlideReview } from '@/lib/api/mentor'

vi.mock('@/lib/api/mentor', () => ({
  mentorApi: {
    reviewSlides: vi.fn(),
    listSlideReviews: vi.fn(),
    applySlideFixes: vi.fn(),
  },
}))

import { mentorApi } from '@/lib/api/mentor'

const REVIEW: SlideReview = {
  id: 'slide_review:r1',
  filename: '提案書.pptx',
  kind: 'pptx',
  page_count: 2,
  overall: 3.1,
  passed: false,
  threshold: 3.0,
  axes: [
    { key: 'logic', score: 4.0, issues: [], passed: true },
    { key: 'message_body', score: 3.5, issues: [], passed: true },
    {
      key: 'charts',
      score: 2.0,
      issues: [{ id: null, page: 2, text: '円グラフが不適切', fix: '横棒に変更', rule: null, applicable: false }],
      passed: false,
    },
    {
      key: 'tone_manner',
      score: 3.0,
      issues: [
        {
          id: 'normalize_fonts@0',
          page: 1,
          text: 'フォント3種',
          fix: 'Meiryoに統一',
          rule: 'normalize_fonts',
          applicable: true,
        },
      ],
      passed: true,
    },
    { key: 'design', score: 3.2, issues: [], passed: true },
  ],
  summary: '構成は良いがグラフに難あり',
  top_fix: '横棒に変更',
  citations: [{ id: 'source:a', title: 'コンサル頭のつくり方' }],
  created: null,
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(mentorApi.listSlideReviews).mockResolvedValue([])
})

describe('MentorSlidesTab', () => {
  it('uploads a file and renders the review with gate badge and axes', async () => {
    vi.mocked(mentorApi.reviewSlides).mockResolvedValue(REVIEW)
    render(<MentorSlidesTab />, { wrapper })

    const input = screen.getByTestId('slide-file-input')
    const file = new File([new Uint8Array([1])], '提案書.pptx')
    fireEvent.change(input, { target: { files: [file] } })

    await waitFor(() =>
      expect(screen.getByTestId('slide-review-result')).toBeInTheDocument()
    )
    expect(mentorApi.reviewSlides).toHaveBeenCalledWith(file)
    expect(screen.getByText('3.1')).toBeInTheDocument()
    expect(screen.getByText(/円グラフが不適切/)).toBeInTheDocument()
    expect(screen.getByText('コンサル頭のつくり方')).toBeInTheDocument()
    // ゲート未達バッジ + 最優先の直し
    expect(screen.getByTestId('axis-charts')).toBeInTheDocument()
    expect(screen.getAllByText(/横棒に変更/).length).toBeGreaterThan(0)
  })

  it('enables apply only after selecting an applicable issue', async () => {
    vi.mocked(mentorApi.reviewSlides).mockResolvedValue(REVIEW)
    vi.mocked(mentorApi.applySlideFixes).mockResolvedValue(new Blob())
    const createObjectURL = vi.fn(() => 'blob:x')
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL,
      revokeObjectURL: vi.fn(),
    })

    render(<MentorSlidesTab />, { wrapper })
    fireEvent.change(screen.getByTestId('slide-file-input'), {
      target: { files: [new File([new Uint8Array([1])], 'deck.pptx')] },
    })
    await waitFor(() =>
      expect(screen.getByTestId('slide-review-result')).toBeInTheDocument()
    )

    // テストの i18n モックはキーをそのまま返す
    const applyButton = screen.getByRole('button', { name: /applyFixes/ })
    expect(applyButton).toBeDisabled()

    fireEvent.click(screen.getByRole('checkbox'))
    expect(applyButton).toBeEnabled()

    fireEvent.click(applyButton)
    await waitFor(() =>
      expect(mentorApi.applySlideFixes).toHaveBeenCalledWith('slide_review:r1', [
        'normalize_fonts@0',
      ])
    )
  })

  it('hands the review off to the chat tab via onDiscuss', async () => {
    vi.mocked(mentorApi.reviewSlides).mockResolvedValue(REVIEW)
    const onDiscuss = vi.fn()
    render(<MentorSlidesTab onDiscuss={onDiscuss} />, { wrapper })
    fireEvent.change(screen.getByTestId('slide-file-input'), {
      target: { files: [new File([new Uint8Array([1])], 'deck.pptx')] },
    })
    await waitFor(() =>
      expect(screen.getByTestId('slide-review-result')).toBeInTheDocument()
    )

    fireEvent.click(screen.getByRole('button', { name: /discussReview/ }))
    expect(onDiscuss).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'slide_review:r1', filename: '提案書.pptx' })
    )
  })

  it('renders the axis radar with the review scores', async () => {
    vi.mocked(mentorApi.reviewSlides).mockResolvedValue(REVIEW)
    render(<MentorSlidesTab />, { wrapper })
    fireEvent.change(screen.getByTestId('slide-file-input'), {
      target: { files: [new File([new Uint8Array([1])], 'deck.pptx')] },
    })
    await waitFor(() =>
      expect(screen.getByTestId('axis-radar')).toBeInTheDocument()
    )
  })

  it('shows history reviews from the list endpoint', async () => {
    vi.mocked(mentorApi.listSlideReviews).mockResolvedValue([
      { ...REVIEW, id: 'slide_review:r2', filename: '旧提案書.pdf', kind: 'pdf' },
    ])
    render(<MentorSlidesTab />, { wrapper })
    await waitFor(() => expect(screen.getByText('旧提案書.pdf')).toBeInTheDocument())
  })
})
