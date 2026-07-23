'use client'

import { useEffect, useRef } from 'react'
import { GraduationCap, Loader2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { MentorComposer } from './MentorComposer'
import { MentorMessage } from './MentorMessage'
import {
  useConsultMentor,
  useMentorMessages,
  useSpeakMessage,
  type DisplayMessage,
} from '@/lib/hooks/use-mentor'
import { useTranslation } from '@/lib/hooks/use-translation'

function EmptyState({ onPick }: { onPick: (question: string) => void }) {
  const { t } = useTranslation()
  const samples = [
    t('mentor.sampleQuestion1'),
    t('mentor.sampleQuestion2'),
    t('mentor.sampleQuestion3'),
  ]
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 p-8 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
        <GraduationCap className="h-6 w-6" />
      </div>
      <p className="max-w-md text-sm text-muted-foreground">{t('mentor.subtitle')}</p>
      <div className="flex flex-wrap justify-center gap-2">
        {samples.map((sample) => (
          <Button
            key={sample}
            variant="outline"
            size="sm"
            className="h-auto whitespace-normal py-1.5 text-xs font-normal"
            onClick={() => onPick(sample)}
          >
            {sample}
          </Button>
        ))}
      </div>
    </div>
  )
}

export function MentorChat({ draft }: { draft?: string }) {
  const { t } = useTranslation()
  const { data: messages, isLoading } = useMentorMessages()
  const consult = useConsultMentor()
  const { speak, speakingId, loadingId } = useSpeakMessage()
  const bottomRef = useRef<HTMLDivElement | null>(null)

  const displayMessages = (messages ?? []) as DisplayMessage[]

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [displayMessages.length, consult.isPending])

  return (
    <div className="flex h-[calc(100vh-14rem)] min-h-[24rem] flex-col gap-4">
      <div className="flex-1 space-y-4 overflow-y-auto pr-1" data-testid="mentor-chat-stream">
        {isLoading ? (
          <div className="flex flex-1 items-center justify-center p-8">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : displayMessages.length === 0 ? (
          <EmptyState onPick={(question) => consult.mutate(question)} />
        ) : (
          <>
            {displayMessages.map((message) => (
              <MentorMessage
                key={message.id}
                message={message}
                onRetry={(content) => consult.mutate(content)}
                onSpeak={speak}
                speaking={speakingId === message.id}
                speakLoading={loadingId === message.id}
              />
            ))}
            {consult.isPending && (
              <div className="flex items-center gap-2 pl-11 text-sm text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                {t('mentor.thinking')}
              </div>
            )}
          </>
        )}
        <div ref={bottomRef} />
      </div>
      <MentorComposer
        onSend={(message) => consult.mutate(message)}
        sending={consult.isPending}
        draft={draft}
      />
    </div>
  )
}
