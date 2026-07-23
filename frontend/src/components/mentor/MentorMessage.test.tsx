import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { MentorMessage } from './MentorMessage'
import type { DisplayMessage } from '@/lib/hooks/use-mentor'

const mentorMessage: DisplayMessage = {
  id: 'mentor_message:m1',
  role: 'mentor',
  content: '**結論**から言うと、良い構成です。',
  sources: ['source:a'],
  sourceRefs: [{ id: 'source:a', title: 'コンサル頭のつくり方' }],
  created: null,
}

describe('MentorMessage', () => {
  it('renders mentor markdown with referenced-book chips and a speak button', () => {
    const onSpeak = vi.fn()
    render(<MentorMessage message={mentorMessage} onSpeak={onSpeak} />)

    expect(screen.getByText('結論')).toBeInTheDocument()
    expect(screen.getByText('コンサル頭のつくり方')).toBeInTheDocument()

    const speakButton = screen.getByRole('button')
    fireEvent.click(speakButton)
    expect(onSpeak).toHaveBeenCalledWith('mentor_message:m1')
  })

  it('hides the speak button for optimistic (unpersisted) answers', () => {
    render(
      <MentorMessage
        message={{ ...mentorMessage, id: 'optimistic:1' }}
        onSpeak={vi.fn()}
      />
    )
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('shows a retry button on failed user messages', () => {
    const onRetry = vi.fn()
    render(
      <MentorMessage
        message={{
          id: 'optimistic:2',
          role: 'user',
          content: '失敗した相談',
          sources: null,
          created: null,
          failed: true,
        }}
        onRetry={onRetry}
      />
    )

    fireEvent.click(screen.getByRole('button'))
    expect(onRetry).toHaveBeenCalledWith('失敗した相談')
  })
})
