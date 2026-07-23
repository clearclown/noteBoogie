import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { MentorComposer } from './MentorComposer'

describe('MentorComposer', () => {
  it('sends the trimmed message on Cmd+Enter and clears the field', () => {
    const onSend = vi.fn()
    render(<MentorComposer onSend={onSend} sending={false} />)
    const textarea = screen.getByRole('textbox')

    fireEvent.change(textarea, { target: { value: '  相談です  ' } })
    fireEvent.keyDown(textarea, { key: 'Enter', metaKey: true })

    expect(onSend).toHaveBeenCalledWith('相談です')
    expect((textarea as HTMLTextAreaElement).value).toBe('')
  })

  it('does not send on plain Enter', () => {
    const onSend = vi.fn()
    render(<MentorComposer onSend={onSend} sending={false} />)
    const textarea = screen.getByRole('textbox')

    fireEvent.change(textarea, { target: { value: '相談' } })
    fireEvent.keyDown(textarea, { key: 'Enter' })

    expect(onSend).not.toHaveBeenCalled()
  })

  it('ignores empty messages and disables while sending', () => {
    const onSend = vi.fn()
    const { rerender } = render(<MentorComposer onSend={onSend} sending={false} />)
    const textarea = screen.getByRole('textbox')

    fireEvent.keyDown(textarea, { key: 'Enter', metaKey: true })
    expect(onSend).not.toHaveBeenCalled()

    rerender(<MentorComposer onSend={onSend} sending={true} />)
    expect(textarea).toBeDisabled()
    expect(screen.getByRole('button')).toBeDisabled()
  })
})
