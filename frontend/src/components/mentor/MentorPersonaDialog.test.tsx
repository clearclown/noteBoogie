import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { MentorPersonaDialog } from './MentorPersonaDialog'

vi.mock('@/lib/api/mentor', () => ({
  mentorApi: {
    getPersona: vi.fn(),
    updatePersona: vi.fn(),
  },
}))

import { mentorApi } from '@/lib/api/mentor'

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('MentorPersonaDialog', () => {
  it('loads the current persona into the editor and saves a new one', async () => {
    vi.mocked(mentorApi.getPersona).mockResolvedValue({
      persona: 'あなたは経験豊富な戦略コンサルタントの師匠です。',
      is_default: false,
    })
    vi.mocked(mentorApi.updatePersona).mockResolvedValue({
      persona: 'あなたは経験豊富な外科医の師匠です。弟子を導きます。',
      is_default: false,
    })
    render(<MentorPersonaDialog />, { wrapper })

    fireEvent.click(screen.getByRole('button', { name: /personaButton/ }))
    const textarea = await screen.findByRole('textbox')
    await waitFor(() =>
      expect((textarea as HTMLTextAreaElement).value).toContain('コンサルタント')
    )

    // 職種は自由: コンサル → 外科医に書き換えて保存できる
    fireEvent.change(textarea, {
      target: { value: 'あなたは経験豊富な外科医の師匠です。弟子を導きます。' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'mentor.personaSave' }))
    await waitFor(() =>
      expect(mentorApi.updatePersona).toHaveBeenCalledWith(
        'あなたは経験豊富な外科医の師匠です。弟子を導きます。'
      )
    )
  })

  it('disables save for too-short personas', async () => {
    vi.mocked(mentorApi.getPersona).mockResolvedValue({
      persona: 'x'.repeat(20),
      is_default: true,
    })
    render(<MentorPersonaDialog />, { wrapper })
    fireEvent.click(screen.getByRole('button', { name: /personaButton/ }))
    const textarea = await screen.findByRole('textbox')
    fireEvent.change(textarea, { target: { value: '短い' } })
    expect(screen.getByRole('button', { name: 'mentor.personaSave' })).toBeDisabled()
  })
})
