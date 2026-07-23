'use client'

import { useCallback, useRef, useState } from 'react'
import { Loader2, Send } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useTranslation } from '@/lib/hooks/use-translation'

interface MentorComposerProps {
  onSend: (message: string) => void
  sending: boolean
}

export function MentorComposer({ onSend, sending }: MentorComposerProps) {
  const { t } = useTranslation()
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  const submit = useCallback(() => {
    const message = value.trim()
    if (!message || sending) return
    onSend(message)
    setValue('')
    textareaRef.current?.focus()
  }, [value, sending, onSend])

  return (
    <div className="flex items-end gap-2 rounded-lg border bg-background p-2">
      <Textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault()
            submit()
          }
        }}
        placeholder={t('mentor.placeholder')}
        className="min-h-[44px] max-h-40 flex-1 resize-none border-0 shadow-none focus-visible:ring-0"
        rows={1}
        disabled={sending}
      />
      <Button
        onClick={submit}
        disabled={sending || !value.trim()}
        size="sm"
        aria-label={t('mentor.send')}
      >
        {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
      </Button>
    </div>
  )
}
