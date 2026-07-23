'use client'

import { useEffect, useState } from 'react'
import { Loader2, UserCog } from 'lucide-react'

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
import { useMentorPersona, useUpdatePersona } from '@/lib/hooks/use-mentor'
import { useTranslation } from '@/lib/hooks/use-translation'

/**
 * ペルソナ編集（汎用化）: 師匠は職種固定ではない。コンサル・外科医・編集者など、
 * 蔵書に合わせて自由に設定できる（相談とスライドレビューの両方に反映）。
 */
export function MentorPersonaDialog() {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState('')
  const { data } = useMentorPersona()
  const update = useUpdatePersona()

  useEffect(() => {
    if (open && data) setValue(data.persona)
  }, [open, data])

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="gap-1.5">
          <UserCog className="h-4 w-4" />
          {t('mentor.personaButton')}
          {data?.is_default === false && (
            <Badge variant="secondary" className="font-normal">
              {t('mentor.personaCustom')}
            </Badge>
          )}
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('mentor.personaTitle')}</DialogTitle>
          <DialogDescription>{t('mentor.personaHelp')}</DialogDescription>
        </DialogHeader>
        <Textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          rows={6}
          placeholder={t('mentor.personaPlaceholder')}
        />
        <DialogFooter>
          <Button
            onClick={() =>
              update.mutate(value.trim(), { onSuccess: () => setOpen(false) })
            }
            disabled={value.trim().length < 10 || update.isPending}
          >
            {update.isPending && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
            {t('mentor.personaSave')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
