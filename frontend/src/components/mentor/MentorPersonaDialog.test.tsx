import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { MentorPersonaDialog } from './MentorPersonaDialog'

vi.mock('@/lib/api/mentor', () => ({
  mentorApi: {
    listPersonas: vi.fn(),
    upsertPersona: vi.fn(),
    activatePersona: vi.fn(),
  },
}))

import { mentorApi } from '@/lib/api/mentor'

const PROFILES = [
  { name: 'default', persona: 'あなたは経験豊富な戦略コンサルタントの師匠です。', active: true },
  { name: 'engineer', persona: 'あなたは経験豊富なシニアエンジニアの師匠です。', active: false },
  { name: 'editor', persona: 'あなたは経験豊富な編集長の師匠です。', active: false },
]

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(mentorApi.listPersonas).mockResolvedValue(PROFILES)
})

describe('MentorPersonaDialog', () => {
  it('shows the active (consultant) persona and switches to a preset', async () => {
    vi.mocked(mentorApi.activatePersona).mockResolvedValue({
      ...PROFILES[1],
      active: true,
    })
    render(<MentorPersonaDialog />, { wrapper })

    fireEvent.click(screen.getByRole('button', { name: /personaButton/ }))
    const textarea = await screen.findByRole('textbox')
    // 既定アクティブはコンサル（そのままで良い）
    await waitFor(() =>
      expect((textarea as HTMLTextAreaElement).value).toContain('コンサルタント')
    )

    // 非コンサル版（engineer）を選ぶと本文が切り替わり、切替ボタンが出る
    fireEvent.click(screen.getByRole('button', { name: 'engineer' }))
    await waitFor(() =>
      expect((textarea as HTMLTextAreaElement).value).toContain('エンジニア')
    )
    fireEvent.click(screen.getByRole('button', { name: 'mentor.personaActivate' }))
    await waitFor(() =>
      expect(mentorApi.activatePersona).toHaveBeenCalledWith('engineer')
    )
  })

  it('saves edited persona text for the selected profile', async () => {
    vi.mocked(mentorApi.upsertPersona).mockResolvedValue({
      ...PROFILES[2],
      persona: '更新後',
    })
    render(<MentorPersonaDialog />, { wrapper })
    fireEvent.click(screen.getByRole('button', { name: /personaButton/ }))
    await screen.findByRole('textbox')

    fireEvent.click(screen.getByRole('button', { name: 'editor' }))
    const textarea = screen.getByRole('textbox')
    fireEvent.change(textarea, {
      target: { value: 'あなたは経験豊富な週刊誌の編集長の師匠です。' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'mentor.personaSave' }))
    await waitFor(() =>
      expect(mentorApi.upsertPersona).toHaveBeenCalledWith(
        'editor',
        'あなたは経験豊富な週刊誌の編集長の師匠です。'
      )
    )
  })

  it('disables save for too-short personas and hides activate for the active one', async () => {
    render(<MentorPersonaDialog />, { wrapper })
    fireEvent.click(screen.getByRole('button', { name: /personaButton/ }))
    const textarea = await screen.findByRole('textbox')

    // アクティブな default 選択中は切替ボタンなし
    expect(
      screen.queryByRole('button', { name: 'mentor.personaActivate' })
    ).not.toBeInTheDocument()

    fireEvent.change(textarea, { target: { value: '短い' } })
    expect(screen.getByRole('button', { name: 'mentor.personaSave' })).toBeDisabled()
  })
})
