import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { QUERY_KEYS } from '@/lib/api/query-client'
import {
  useActivatePersona,
  useApplySlideFixes,
  useConsultMentor,
  useDeleteMemory,
  useMentorMessages,
  useMentorPersonas,
  useReviewSlides,
  useSlideReviews,
  type DisplayMessage,
} from './use-mentor'

vi.mock('@/lib/api/mentor', () => ({
  mentorApi: {
    consult: vi.fn(),
    getMessages: vi.fn(),
    getMemories: vi.fn(),
    deleteMemory: vi.fn(),
    speak: vi.fn(),
    getWeights: vi.fn(),
    updateWeight: vi.fn(),
    listSlideReviews: vi.fn(),
    reviewSlides: vi.fn(),
    applySlideFixes: vi.fn(),
    listPersonas: vi.fn(),
    upsertPersona: vi.fn(),
    activatePersona: vi.fn(),
  },
}))

import { mentorApi } from '@/lib/api/mentor'

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return { client, wrapper }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('useMentorMessages', () => {
  it('loads the persisted conversation log', async () => {
    vi.mocked(mentorApi.getMessages).mockResolvedValue([
      { id: 'mentor_message:1', role: 'user', content: '質問', sources: null, created: null },
    ])
    const { wrapper } = makeWrapper()
    const { result } = renderHook(() => useMentorMessages(), { wrapper })
    await waitFor(() => expect(result.current.data).toHaveLength(1))
  })
})

describe('useConsultMentor', () => {
  it('optimistically shows the user message, then appends the mentor answer', async () => {
    type ConsultResponse = Awaited<ReturnType<typeof mentorApi.consult>>
    let resolveConsult: (value: ConsultResponse) => void = () => {}
    vi.mocked(mentorApi.consult).mockImplementation(
      () => new Promise<ConsultResponse>((resolve) => { resolveConsult = resolve })
    )
    const { client, wrapper } = makeWrapper()
    client.setQueryData(QUERY_KEYS.mentorMessages, [])

    const { result } = renderHook(() => useConsultMentor(), { wrapper })
    act(() => result.current.mutate('相談です'))

    await waitFor(() => {
      const messages = client.getQueryData<DisplayMessage[]>(QUERY_KEYS.mentorMessages)
      expect(messages).toHaveLength(1)
      expect(messages?.[0]).toMatchObject({ role: 'user', content: '相談です', pending: true })
    })

    act(() => {
      resolveConsult({
        answer: '結論から言うと…',
        sources: [{ id: 'source:a', title: '本A' }],
        message_id: 'mentor_message:m1',
      })
    })

    await waitFor(() => {
      const messages = client.getQueryData<DisplayMessage[]>(QUERY_KEYS.mentorMessages)
      expect(messages).toHaveLength(2)
      expect(messages?.[0].pending).toBe(false)
      expect(messages?.[1]).toMatchObject({
        id: 'mentor_message:m1',
        role: 'mentor',
        content: '結論から言うと…',
      })
      expect(messages?.[1].sourceRefs?.[0].title).toBe('本A')
    })
  })

  it('marks the optimistic message as failed on error (retryable)', async () => {
    vi.mocked(mentorApi.consult).mockRejectedValue(new Error('boom'))
    const { client, wrapper } = makeWrapper()
    client.setQueryData(QUERY_KEYS.mentorMessages, [])

    const { result } = renderHook(() => useConsultMentor(), { wrapper })
    act(() => result.current.mutate('失敗する相談'))

    await waitFor(() => {
      const messages = client.getQueryData<DisplayMessage[]>(QUERY_KEYS.mentorMessages)
      expect(messages).toHaveLength(1)
      expect(messages?.[0]).toMatchObject({ failed: true, pending: false })
    })
  })
})

describe('useDeleteMemory', () => {
  it('invalidates the memories query after deletion', async () => {
    vi.mocked(mentorApi.deleteMemory).mockResolvedValue(undefined)
    const { client, wrapper } = makeWrapper()
    const invalidate = vi.spyOn(client, 'invalidateQueries')

    const { result } = renderHook(() => useDeleteMemory(), { wrapper })
    act(() => result.current.mutate('mentor_memory:x'))

    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({ queryKey: QUERY_KEYS.mentorMemories })
    )
  })
})

describe('slide review hooks', () => {
  it('useSlideReviews loads history', async () => {
    vi.mocked(mentorApi.listSlideReviews).mockResolvedValue([
      { id: 'slide_review:r1' } as never,
    ])
    const { wrapper } = makeWrapper()
    const { result } = renderHook(() => useSlideReviews(), { wrapper })
    await waitFor(() => expect(result.current.data).toHaveLength(1))
  })

  it('useReviewSlides uploads and invalidates the history', async () => {
    vi.mocked(mentorApi.reviewSlides).mockResolvedValue({ id: 'slide_review:r2' } as never)
    const { client, wrapper } = makeWrapper()
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    const { result } = renderHook(() => useReviewSlides(), { wrapper })
    const file = new File([new Uint8Array([1])], 'deck.pdf')
    act(() => result.current.mutate(file))
    await waitFor(() => {
      expect(mentorApi.reviewSlides).toHaveBeenCalledWith(file)
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: QUERY_KEYS.slideReviews,
      })
    })
  })

  it('useApplySlideFixes downloads the coached deck as <name>_coached.pptx', async () => {
    vi.mocked(mentorApi.applySlideFixes).mockResolvedValue(new Blob([new Uint8Array([1])]))
    const createObjectURL = vi.fn(() => 'blob:coached')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => {})

    const { wrapper } = makeWrapper()
    const { result } = renderHook(() => useApplySlideFixes(), { wrapper })
    act(() =>
      result.current.mutate({
        reviewId: 'slide_review:r1',
        issueIds: ['normalize_fonts@0'],
        filename: '提案書.pptx',
      })
    )
    await waitFor(() => {
      expect(mentorApi.applySlideFixes).toHaveBeenCalledWith('slide_review:r1', [
        'normalize_fonts@0',
      ])
      expect(click).toHaveBeenCalled()
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:coached')
    })
    click.mockRestore()
    vi.unstubAllGlobals()
  })
})

describe('persona hooks', () => {
  it('useMentorPersonas lists profiles', async () => {
    vi.mocked(mentorApi.listPersonas).mockResolvedValue([
      { name: 'default', persona: 'p', active: true },
    ])
    const { wrapper } = makeWrapper()
    const { result } = renderHook(() => useMentorPersonas(), { wrapper })
    await waitFor(() => expect(result.current.data?.[0].name).toBe('default'))
  })

  it('useActivatePersona switches and invalidates', async () => {
    vi.mocked(mentorApi.activatePersona).mockResolvedValue({
      name: 'engineer',
      persona: 'p',
      active: true,
    })
    const { client, wrapper } = makeWrapper()
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    const { result } = renderHook(() => useActivatePersona(), { wrapper })
    act(() => result.current.mutate('engineer'))
    await waitFor(() => {
      expect(mentorApi.activatePersona).toHaveBeenCalledWith('engineer')
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: QUERY_KEYS.mentorPersona,
      })
    })
  })
})
