'use client'

import { Brain, Loader2, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useDeleteMemory, useMentorMemories } from '@/lib/hooks/use-mentor'
import { useTranslation } from '@/lib/hooks/use-translation'

function formatDay(created: string | null): string {
  if (!created) return ''
  const date = new Date(created)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString()
}

export function MentorMemoryPanel() {
  const { t } = useTranslation()
  const { data: memories, isLoading } = useMentorMemories()
  const deleteMemory = useDeleteMemory()

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className="gap-1.5">
          <Brain className="h-4 w-4" />
          {t('mentor.memoryButton')}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-96 p-0">
        <div className="border-b px-4 py-3 text-sm font-medium">
          {t('mentor.memoryTitle')}
        </div>
        <ScrollArea className="max-h-80">
          {isLoading ? (
            <div className="flex justify-center p-4">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            </div>
          ) : !memories || memories.length === 0 ? (
            <p className="p-4 text-sm text-muted-foreground">
              {t('mentor.memoryEmpty')}
            </p>
          ) : (
            <ul className="divide-y">
              {memories.map((memory) => (
                <li key={memory.id} className="flex items-start gap-2 px-4 py-3">
                  <div className="min-w-0 flex-1 space-y-0.5">
                    <p className="truncate text-sm font-medium">{memory.question}</p>
                    <p className="line-clamp-2 text-xs text-muted-foreground">
                      {memory.gist}
                    </p>
                    <p className="text-[11px] text-muted-foreground/70">
                      {formatDay(memory.created)}
                    </p>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 w-7 shrink-0 p-0 text-muted-foreground hover:text-destructive"
                    aria-label={t('mentor.memoryDelete')}
                    onClick={() => deleteMemory.mutate(memory.id)}
                    disabled={deleteMemory.isPending}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </ScrollArea>
      </PopoverContent>
    </Popover>
  )
}
