'use client'

import { useEffect, useState } from 'react'
import { Check, Loader2, UserCog } from 'lucide-react'

import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import {
  useActivatePersona,
  useMentorPersonas,
  useUpsertPersona,
} from '@/lib/hooks/use-mentor'
import { useTranslation } from '@/lib/hooks/use-translation'

/**
 * ペルソナ切替+編集。コンサル（default）が既定アクティブだが、
 * generalist/engineer/editor/researcher などのプリセットや自作へ
 * ワンクリックで切り替えられる（相談・スライドレビューに即時反映）。
 */
export function MentorPersonaDialog() {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [selectedName, setSelectedName] = useState<string | null>(null)
  const [value, setValue] = useState('')
  const { data: profiles } = useMentorPersonas()
  const upsert = useUpsertPersona()
  const activate = useActivatePersona()

  const activeProfile = profiles?.find((p) => p.active)
  const selected =
    profiles?.find((p) => p.name === selectedName) ?? activeProfile ?? profiles?.[0]

  useEffect(() => {
    if (open && selected) setValue(selected.persona)
    // selected?.name で選択切替時にも本文を読み直す
  }, [open, selected?.name]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="gap-1.5">
          <UserCog className="h-4 w-4" />
          {t('mentor.personaButton')}
          {activeProfile && activeProfile.name !== 'default' && (
            <Badge variant="secondary" className="font-normal">
              {activeProfile.name}
            </Badge>
          )}
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{t('mentor.personaTitle')}</DialogTitle>
          <DialogDescription>{t('mentor.personaHelp')}</DialogDescription>
        </DialogHeader>

        <div className="flex flex-wrap gap-1.5" data-testid="persona-profiles">
          {(profiles ?? []).map((profile) => (
            <Button
              key={profile.name}
              variant={profile.name === selected?.name ? 'default' : 'outline'}
              size="sm"
              className={cn('gap-1', profile.active && 'font-semibold')}
              onClick={() => setSelectedName(profile.name)}
            >
              {profile.active && <Check className="h-3.5 w-3.5" />}
              {profile.name}
            </Button>
          ))}
        </div>

        <Textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          rows={6}
          placeholder={t('mentor.personaPlaceholder')}
        />
        <DialogFooter className="gap-2">
          {selected && !selected.active && (
            <Button
              variant="outline"
              onClick={() =>
                activate.mutate(selected.name, { onSuccess: () => setOpen(false) })
              }
              disabled={activate.isPending}
            >
              {activate.isPending && (
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              )}
              {t('mentor.personaActivate')}
            </Button>
          )}
          <Button
            onClick={() =>
              selected &&
              upsert.mutate({ name: selected.name, persona: value.trim() })
            }
            disabled={!selected || value.trim().length < 10 || upsert.isPending}
          >
            {upsert.isPending && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
            {t('mentor.personaSave')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
