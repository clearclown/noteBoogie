'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowLeft,
  BookUp,
  Headphones,
  Image as ImageIcon,
  Loader2,
  Pause,
  Plus,
  Play,
  SkipBack,
  SkipForward,
  ThumbsDown,
  ThumbsUp,
  Trash2,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
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
import { audiobooksApi } from '@/lib/api/audiobooks'
import { podcastsApi } from '@/lib/api/podcasts'
import { ImportBookDialog } from './ImportBookDialog'
import { AudiobookPlayerControls } from './AudiobookPlayerControls'
import { getApiUrl } from '@/lib/config'
import {
  AUDIOBOOK_QUERY_KEYS,
  useAudiobook,
  useAudiobookFigures,
  useAudiobooks,
  useGenerateAudiobook,
} from '@/lib/hooks/use-audiobooks'
import { sourcesApi } from '@/lib/api/sources'
import { useEpisodeProfiles, useSpeakerProfiles } from '@/lib/hooks/use-podcasts'
import { useAudiobookPlayerStore } from '@/lib/stores/audiobook-player-store'
import { useTranslation } from '@/lib/hooks/use-translation'
import { AudiobookChapter } from '@/lib/types/audiobooks'
import { useQuery, useQueryClient } from '@tanstack/react-query'

/** Fetch a chapter's protected audio as an object URL (same auth pattern as EpisodeCard). */
async function fetchChapterAudio(chapterId: string): Promise<string> {
  const base = await getApiUrl()
  let token: string | undefined
  if (typeof window !== 'undefined') {
    const raw = window.localStorage.getItem('auth-storage')
    if (raw) {
      try {
        token = JSON.parse(raw)?.state?.token
      } catch {
        // ignore parse errors; request proceeds unauthenticated
      }
    }
  }
  const headers: HeadersInit = {}
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }
  const response = await fetch(
    `${base}/api/podcasts/episodes/${encodeURIComponent(chapterId)}/audio`,
    { headers }
  )
  if (!response.ok) {
    throw new Error(`Audio request failed with status ${response.status}`)
  }
  return URL.createObjectURL(await response.blob())
}

function AudiobookDetailView({
  audiobookId,
  onBack,
}: {
  audiobookId: string
  onBack: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { data: detail, isLoading } = useAudiobook(audiobookId)
  const { data: figures } = useAudiobookFigures(audiobookId)

  const [currentIndex, setCurrentIndex] = useState<number | null>(null)
  const autoAdvance = useAudiobookPlayerStore((s) => s.autoAdvance)
  const setAutoAdvance = useAudiobookPlayerStore((s) => s.setAutoAdvance)
  const setPosition = useAudiobookPlayerStore((s) => s.setPosition)
  const [audioSrc, setAudioSrc] = useState<string | undefined>()
  const [audioError, setAudioError] = useState(false)
  const [playing, setPlaying] = useState(false)
  const audioRef = useRef<HTMLAudioElement | null>(null)

  const chapters = useMemo(() => detail?.chapters ?? [], [detail])
  const playable = useCallback(
    (index: number | null) =>
      index !== null && Boolean(chapters[index]?.audio_file && chapters[index]?.id),
    [chapters]
  )

  // Remember the listening position (persisted).
  useEffect(() => {
    setPosition(audiobookId, currentIndex)
  }, [audiobookId, currentIndex, setPosition])

  // Load the selected chapter's audio blob.
  useEffect(() => {
    let revokeUrl: string | undefined
    setAudioError(false)
    setAudioSrc(undefined)
    if (!playable(currentIndex)) {
      return
    }
    const chapter = chapters[currentIndex as number]
    fetchChapterAudio(chapter.id as string)
      .then((url) => {
        revokeUrl = url
        setAudioSrc(url)
      })
      .catch((error) => {
        console.error('Unable to load chapter audio', error)
        setAudioError(true)
      })
    return () => {
      if (revokeUrl) {
        URL.revokeObjectURL(revokeUrl)
      }
    }
  }, [currentIndex, chapters, playable])

  // Autoplay once the blob is ready (also drives auto-advance).
  useEffect(() => {
    if (audioSrc && audioRef.current) {
      void audioRef.current.play().catch(() => setPlaying(false))
    }
  }, [audioSrc])

  const advance = useCallback(
    (step: number) => {
      if (currentIndex === null) {
        return
      }
      let next = currentIndex + step
      while (next >= 0 && next < chapters.length && !playable(next)) {
        next += step
      }
      if (next >= 0 && next < chapters.length) {
        setCurrentIndex(next)
      }
    },
    [chapters.length, currentIndex, playable]
  )

  const handleEnded = () => {
    setPlaying(false)
    if (autoAdvance) {
      advance(1)
    }
  }

  const togglePlay = () => {
    const el = audioRef.current
    if (!el) {
      return
    }
    if (el.paused) {
      void el.play()
    } else {
      el.pause()
    }
  }

  const figuresByChapter = useMemo(() => {
    const groups = new Map<number | null, NonNullable<typeof figures>>()
    for (const figure of figures ?? []) {
      const key = figure.chapter_index
      const bucket = groups.get(key) ?? []
      bucket.push(figure)
      groups.set(key, bucket)
    }
    return groups
  }, [figures])

  const currentChapter: AudiobookChapter | undefined =
    currentIndex !== null ? chapters[currentIndex] : undefined
  const currentFigures =
    (currentChapter ? figuresByChapter.get(currentChapter.chapter_index) : undefined) ?? []

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <h2 className="text-lg font-semibold">{detail?.name}</h2>
        <label className="ml-auto flex items-center gap-2 text-sm text-muted-foreground cursor-pointer">
          <Checkbox
            checked={autoAdvance}
            onCheckedChange={(checked) => setAutoAdvance(checked === true)}
          />
          <span>{t('podcasts.audiobookAutoAdvance')}</span>
        </label>
      </div>

      {/* Player */}
      <Card>
        <CardContent className="pt-6 space-y-3">
          <div className="flex items-center justify-center gap-4">
            <Button
              variant="outline"
              size="icon"
              onClick={() => advance(-1)}
              disabled={currentIndex === null}
              aria-label={t('podcasts.audiobookPrevChapter')}
            >
              <SkipBack className="h-5 w-5" />
            </Button>
            <Button
              size="icon"
              className="h-14 w-14 rounded-full"
              onClick={togglePlay}
              disabled={!audioSrc}
              aria-label={playing ? t('podcasts.audiobookPause') : t('podcasts.audiobookPlay')}
            >
              {playing ? <Pause className="h-7 w-7" /> : <Play className="h-7 w-7" />}
            </Button>
            <Button
              variant="outline"
              size="icon"
              onClick={() => advance(1)}
              disabled={currentIndex === null}
              aria-label={t('podcasts.audiobookNextChapter')}
            >
              <SkipForward className="h-5 w-5" />
            </Button>
          </div>
          <p className="text-center text-sm text-muted-foreground min-h-5">
            {audioError
              ? t('podcasts.audioUnavailable')
              : currentChapter?.name ?? t('podcasts.audiobookSelectChapter')}
          </p>
          {audioSrc ? (
            <>
              {/* ネイティブ controls は使わず、スマホ最適のカスタム操作を重ねる */}
              <audio
                ref={audioRef}
                src={audioSrc}
                className="hidden"
                onPlay={() => setPlaying(true)}
                onPause={() => setPlaying(false)}
                onEnded={handleEnded}
              />
              <AudiobookPlayerControls audioRef={audioRef} audioSrc={audioSrc} />
            </>
          ) : null}
        </CardContent>
      </Card>

      {/* Tracklist */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t('podcasts.audiobookChapters')}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-1">
          {chapters.map((chapter, index) => {
            const ready = Boolean(chapter.audio_file)
            const active = index === currentIndex
            return (
              <button
                key={chapter.id ?? index}
                type="button"
                onClick={() => ready && setCurrentIndex(index)}
                disabled={!ready}
                className={`w-full flex items-center gap-3 rounded-md px-3 py-2 text-left text-sm transition-colors ${
                  active
                    ? 'bg-primary/10 text-primary'
                    : ready
                      ? 'hover:bg-muted'
                      : 'opacity-50 cursor-not-allowed'
                }`}
              >
                <span className="w-6 text-center font-mono text-xs text-muted-foreground">
                  {(chapter.chapter_index ?? index) + 1}
                </span>
                <span className="flex-1 truncate">{chapter.name}</span>
                {ready ? (
                  <span className="flex items-center gap-0.5 shrink-0">
                    {active && playing ? <Headphones className="h-4 w-4" /> : null}
                    {(['up', 'down'] as const).map((rating) => {
                      const selected = chapter.feedback === rating
                      const Icon = rating === 'up' ? ThumbsUp : ThumbsDown
                      return (
                        <Button
                          key={rating}
                          variant="ghost"
                          size="sm"
                          className="h-6 w-6 p-0 text-muted-foreground"
                          aria-label={t(
                            rating === 'up'
                              ? 'podcasts.chapterFeedbackUp'
                              : 'podcasts.chapterFeedbackDown'
                          )}
                          aria-pressed={selected}
                          onClick={(event) => {
                            event.stopPropagation()
                            // 同じ評価をもう一度押すと取り消し（null）
                            void podcastsApi
                              .setEpisodeFeedback(
                                chapter.id as string,
                                selected ? null : rating
                              )
                              .then(() =>
                                queryClient.invalidateQueries({
                                  queryKey:
                                    AUDIOBOOK_QUERY_KEYS.audiobook(audiobookId),
                                })
                              )
                              .catch((error) =>
                                console.error('Failed to set feedback', error)
                              )
                          }}
                        >
                          <Icon
                            className={
                              selected
                                ? rating === 'up'
                                  ? 'h-3.5 w-3.5 fill-current text-emerald-600'
                                  : 'h-3.5 w-3.5 fill-current text-destructive'
                                : 'h-3.5 w-3.5'
                            }
                          />
                        </Button>
                      )
                    })}
                  </span>
                ) : chapter.generation_error ? (
                  <span className="flex items-center gap-1 shrink-0">
                    <Badge variant="destructive" title={chapter.generation_error}>
                      {t('podcasts.audiobookFailed')}
                    </Badge>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 px-2 text-xs"
                      aria-label={t('podcasts.audiobookRetry')}
                      onClick={(event) => {
                        event.stopPropagation()
                        void audiobooksApi
                          .retryChapter(chapter.id as string)
                          .then(() =>
                            queryClient.invalidateQueries({
                              queryKey: AUDIOBOOK_QUERY_KEYS.audiobook(audiobookId),
                            })
                          )
                          .catch((error) =>
                            console.error('Failed to retry chapter', error)
                          )
                      }}
                    >
                      {t('podcasts.audiobookRetry')}
                    </Button>
                  </span>
                ) : (
                  <Badge variant="outline" className="shrink-0">
                    {t('podcasts.audiobookAudioPending')}
                  </Badge>
                )}
              </button>
            )
          })}
        </CardContent>
      </Card>

      {/* Figure gallery for the playing chapter (falls back to all figures) */}
      {(figures?.length ?? 0) > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <ImageIcon className="h-4 w-4" />
              {t('podcasts.audiobookFigures')}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {(currentFigures.length > 0 ? currentFigures : (figures ?? [])).map(
                (figure) =>
                  figure.id ? (
                    <figure key={figure.id} className="space-y-1">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={audiobooksApi.figureImageUrl(figure.id)}
                        alt={figure.caption ?? ''}
                        loading="lazy"
                        className="w-full rounded-md border object-contain bg-muted"
                      />
                      {figure.caption ? (
                        <figcaption className="text-xs text-muted-foreground line-clamp-3">
                          {figure.caption}
                        </figcaption>
                      ) : null}
                    </figure>
                  ) : null
              )}
            </div>
          </CardContent>
        </Card>
      ) : null}
    </div>
  )
}

function GenerateAudiobookDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useTranslation()
  const generate = useGenerateAudiobook()
  const [sourceId, setSourceId] = useState<string>('')
  const [name, setName] = useState('')
  const [episodeProfile, setEpisodeProfile] = useState('book_navigator')
  const [speakerProfile, setSpeakerProfile] = useState('book_navigator_mentor')
  const { data: sources } = useQuery({
    queryKey: ['sources', 'all-for-audiobook'],
    queryFn: () => sourcesApi.list({ limit: 100 }),
    enabled: open,
  })
  // Model/voice presets = existing episode & speaker profiles (editable in
  // the Templates tab), so cost/quality is the user's choice per generation.
  const { episodeProfiles } = useEpisodeProfiles()
  const { speakerProfiles } = useSpeakerProfiles(episodeProfiles)

  const handleSubmit = async () => {
    if (!sourceId || !name.trim()) {
      return
    }
    try {
      await generate.mutateAsync({
        audiobook_name: name.trim(),
        source_id: sourceId,
        episode_profile: episodeProfile,
        speaker_profile: speakerProfile,
      })
      onOpenChange(false)
    } catch (error) {
      console.error('Failed to start audiobook generation', error)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('podcasts.audiobookGenerateTitle')}</DialogTitle>
          <DialogDescription>{t('podcasts.audiobookGenerateDesc')}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <label className="block text-sm font-medium">
            {t('podcasts.audiobookSourceLabel')}
            <select
              className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm"
              value={sourceId}
              onChange={(event) => {
                setSourceId(event.target.value)
                const src = sources?.find((s) => s.id === event.target.value)
                if (src?.title && !name) {
                  setName(src.title)
                }
              }}
            >
              <option value="">—</option>
              {(sources ?? []).map((source) => (
                <option key={source.id} value={source.id}>
                  {source.title ?? source.id}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm font-medium">
            {t('podcasts.audiobookNameLabel')}
            <Input
              className="mt-1"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="block text-sm font-medium">
              {t('podcasts.audiobookScriptProfile')}
              <select
                className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm"
                value={episodeProfile}
                onChange={(event) => setEpisodeProfile(event.target.value)}
              >
                {(episodeProfiles ?? []).map((profile) => (
                  <option key={profile.id} value={profile.name}>
                    {profile.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-sm font-medium">
              {t('podcasts.audiobookVoiceProfile')}
              <select
                className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm"
                value={speakerProfile}
                onChange={(event) => setSpeakerProfile(event.target.value)}
              >
                {(speakerProfiles ?? []).map((profile) => (
                  <option key={profile.id} value={profile.name}>
                    {profile.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {generate.isError ? (
            <p className="text-sm text-destructive">
              {t('podcasts.audiobookGenerateError')}
            </p>
          ) : null}
        </div>
        <DialogFooter>
          <Button
            onClick={() => void handleSubmit()}
            disabled={!sourceId || !name.trim() || generate.isPending}
          >
            {generate.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : null}
            {t('podcasts.audiobookGenerateStart')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function AudiobooksTab() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { data: audiobooks, isLoading } = useAudiobooks()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [generateOpen, setGenerateOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)

  const handleDelete = async (audiobookId: string) => {
    setDeletingId(audiobookId)
    try {
      await audiobooksApi.delete(audiobookId)
      await queryClient.invalidateQueries({ queryKey: AUDIOBOOK_QUERY_KEYS.audiobooks })
    } catch (error) {
      console.error('Failed to delete audiobook', error)
    } finally {
      setDeletingId(null)
    }
  }

  if (selectedId) {
    return <AudiobookDetailView audiobookId={selectedId} onBack={() => setSelectedId(null)} />
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const generateBar = (
    <div className="flex flex-wrap justify-end gap-2">
      <Button size="sm" variant="outline" onClick={() => setImportOpen(true)}>
        <BookUp className="h-4 w-4" />
        {t('podcasts.importBook')}
      </Button>
      <Button size="sm" onClick={() => setGenerateOpen(true)}>
        <Plus className="h-4 w-4" />
        {t('podcasts.audiobookGenerate')}
      </Button>
      <GenerateAudiobookDialog open={generateOpen} onOpenChange={setGenerateOpen} />
      <ImportBookDialog open={importOpen} onOpenChange={setImportOpen} />
    </div>
  )

  if (!audiobooks || audiobooks.length === 0) {
    return (
      <div className="space-y-4">
        {generateBar}
        <div className="rounded-lg border border-dashed p-10 text-center text-muted-foreground">
          <Headphones className="mx-auto mb-3 h-8 w-8" />
          <p>{t('podcasts.audiobooksEmpty')}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {generateBar}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {audiobooks.map((audiobook) =>
        audiobook.id ? (
          <Card
            key={audiobook.id}
            className="cursor-pointer hover:border-primary/50 transition-colors"
            onClick={() => setSelectedId(audiobook.id)}
          >
            <CardHeader>
              <CardTitle className="text-base flex items-start justify-between gap-2">
                <span className="line-clamp-2">{audiobook.name}</span>
                <Button
                  variant="ghost"
                  size="icon"
                  className="shrink-0 h-8 w-8 text-muted-foreground"
                  disabled={deletingId === audiobook.id}
                  onClick={(event) => {
                    event.stopPropagation()
                    void handleDelete(audiobook.id as string)
                  }}
                  aria-label={t('common.delete')}
                >
                  {deletingId === audiobook.id ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Trash2 className="h-4 w-4" />
                  )}
                </Button>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <Badge variant="secondary">
                {t('podcasts.audiobookChapterCount').replace(
                  '{{count}}',
                  String(audiobook.chapter_count ?? 0)
                )}
              </Badge>
            </CardContent>
          </Card>
        ) : null
      )}
      </div>
    </div>
  )
}
