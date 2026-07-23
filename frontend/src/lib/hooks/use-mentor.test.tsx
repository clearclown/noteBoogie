import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { QUERY_KEYS } from '@/lib/api/query-client'
import {
  useConsultMentor,
  useDeleteMemory,
  useMentorMessages,
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
