'use client'

import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { BookUp, CheckCircle2, Loader2, XCircle } from 'lucide-react'

import apiClient from '@/lib/api/client'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { AUDIOBOOK_QUERY_KEYS } from '@/lib/hooks/use-audiobooks'
import { useTranslation } from '@/lib/hooks/use-translation'

const POLL_MS = 5000

type Phase = 'idle' | 'uploading' | 'running' | 'done' | 'failed'

/**
 * NotebookLM 流「ブラウザに PDF を置くだけ」の取り込み。
 * アップロード → 変換+取り込みジョブ → /commands/jobs/{id} ポーリング。
 * 変換はホスト worker の YomiToku(MPS) が実行（400頁で30分前後）。
 */
export function ImportBookDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [captions, setCaptions] = useState(true)
  const [phase, setPhase] = useState<Phase>('idle')
  const [error, setError] = useState<string | null>(null)
  const jobRef = useRef<string | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [])

  const poll = () => {
    timerRef.current = setInterval(async () => {
      if (!jobRef.current) return
      try {
        const { data } = await apiClient.get(
          `/commands/jobs/${encodeURIComponent(jobRef.current)}`
        )
        const status = String(data.status ?? '')
        if (status === 'completed') {
          if (timerRef.current) clearInterval(timerRef.current)
          setPhase('done')
          queryClient.invalidateQueries({ queryKey: AUDIOBOOK_QUERY_KEYS.audiobooks })
          queryClient.invalidateQueries({ queryKey: ['sources'] })
        } else if (status === 'failed' || status === 'error') {
          if (timerRef.current) clearInterval(timerRef.current)
          setPhase('failed')
          setError(String(data.error_message ?? data.result?.message ?? status))
        }
      } catch {
        // 一時的なポーリング失敗は無視して次のティックへ
      }
    }, POLL_MS)
  }

  const submit = async () => {
    if (!file) return
    setPhase('uploading')
    setError(null)
    try {
      const form = new FormData()
      form.append('file', file)
      if (title.trim()) form.append('title', title.trim())
      form.append('captions', String(captions))
      const { data } = await apiClient.post('/books/import', form)
      jobRef.current = String(data.job_id)
      setPhase('running')
      poll()
    } catch (e) {
      setPhase('failed')
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const busy = phase === 'uploading' || phase === 'running'

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!busy) onOpenChange(next)
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('podcasts.importBookTitle')}</DialogTitle>
          <DialogDescription>{t('podcasts.importBookDesc')}</DialogDescription>
        </DialogHeader>

        {phase === 'done' ? (
          <div className="flex items-center gap-2 rounded-md bg-emerald-50 p-3 text-sm text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-400">
            <CheckCircle2 className="h-4 w-4 shrink-0" />
            {t('podcasts.importBookDone')}
          </div>
        ) : phase === 'failed' ? (
          <div className="flex items-start gap-2 rounded-md bg-destructive/10 p-3 text-sm">
            <XCircle className="h-4 w-4 shrink-0 text-destructive" />
            <span className="break-all">{error ?? t('podcasts.importBookError')}</span>
          </div>
        ) : phase === 'running' ? (
          <div className="flex items-center gap-2 rounded-md bg-muted p-3 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
            {t('podcasts.importBookRunning')}
          </div>
        ) : (
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="book-pdf">{t('podcasts.importBookFile')}</Label>
              <Input
                id="book-pdf"
                type="file"
                accept=".pdf"
                data-testid="import-book-file"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="book-title">{t('podcasts.importBookName')}</Label>
              <Input
                id="book-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder={file?.name.replace(/\.pdf$/i, '') ?? ''}
              />
            </div>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={captions}
                onCheckedChange={(checked) => setCaptions(checked === true)}
              />
              {t('podcasts.importBookCaptions')}
            </label>
          </div>
        )}

        <DialogFooter>
          {phase === 'done' || phase === 'failed' ? (
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {t('common.close')}
            </Button>
          ) : (
            <Button onClick={submit} disabled={!file || busy}>
              {busy ? (
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              ) : (
                <BookUp className="mr-1.5 h-4 w-4" />
              )}
              {t('podcasts.importBookStart')}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
