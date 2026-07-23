'use client'

import { RefObject, useEffect, useState } from 'react'
import { RotateCcw, RotateCw, Volume2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { useAudiobookPlayerStore } from '@/lib/stores/audiobook-player-store'
import { useTranslation } from '@/lib/hooks/use-translation'

// 音楽プレーヤ標準の速度段（タップで循環）
export const PLAYBACK_RATES = [0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
const SKIP_SECONDS = 10

export function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00'
  const total = Math.floor(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  return h > 0
    ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${m}:${String(s).padStart(2, '0')}`
}

export function nextRate(current: number): number {
  const index = PLAYBACK_RATES.findIndex((r) => Math.abs(r - current) < 0.01)
  return PLAYBACK_RATES[(index + 1) % PLAYBACK_RATES.length] ?? 1.0
}

/**
 * スマホ前提のカスタム再生コントロール（ネイティブ <audio controls> の代替）。
 * シーク・±10秒・再生速度（永続化）・音量（デスクトップのみ、iOSはOS音量）。
 */
export function AudiobookPlayerControls({
  audioRef,
  audioSrc,
}: {
  audioRef: RefObject<HTMLAudioElement | null>
  audioSrc: string | null
}) {
  const { t } = useTranslation()
  const playbackRate = useAudiobookPlayerStore((s) => s.playbackRate)
  const volume = useAudiobookPlayerStore((s) => s.volume)
  const setPlaybackRate = useAudiobookPlayerStore((s) => s.setPlaybackRate)
  const setVolume = useAudiobookPlayerStore((s) => s.setVolume)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)

  // 要素イベント → 表示状態。src が替わるたびに購読し直し、速度/音量を適用
  useEffect(() => {
    const el = audioRef.current
    if (!el) return
    el.playbackRate = playbackRate
    el.volume = volume
    setCurrentTime(0)
    setDuration(Number.isFinite(el.duration) ? el.duration : 0)

    const onTime = () => setCurrentTime(el.currentTime)
    const onMeta = () => {
      setDuration(Number.isFinite(el.duration) ? el.duration : 0)
      el.playbackRate = playbackRate // 一部ブラウザは load で速度をリセットする
    }
    el.addEventListener('timeupdate', onTime)
    el.addEventListener('loadedmetadata', onMeta)
    el.addEventListener('durationchange', onMeta)
    return () => {
      el.removeEventListener('timeupdate', onTime)
      el.removeEventListener('loadedmetadata', onMeta)
      el.removeEventListener('durationchange', onMeta)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [audioSrc, audioRef])

  useEffect(() => {
    if (audioRef.current) audioRef.current.playbackRate = playbackRate
  }, [playbackRate, audioRef])

  useEffect(() => {
    if (audioRef.current) audioRef.current.volume = volume
  }, [volume, audioRef])

  const skip = (delta: number) => {
    const el = audioRef.current
    if (!el) return
    const max = Number.isFinite(el.duration) ? el.duration : Infinity
    el.currentTime = Math.min(max, Math.max(0, el.currentTime + delta))
    setCurrentTime(el.currentTime)
  }

  const seek = (value: number) => {
    const el = audioRef.current
    if (!el) return
    el.currentTime = value
    setCurrentTime(value)
  }

  if (!audioSrc) return null

  return (
    <div className="space-y-2" data-testid="player-controls">
      {/* シークバー + 時刻 */}
      <div className="flex items-center gap-2 text-xs tabular-nums text-muted-foreground">
        <span className="w-12 text-right">{formatTime(currentTime)}</span>
        <input
          type="range"
          min={0}
          max={duration || 0}
          step={1}
          value={Math.min(currentTime, duration || 0)}
          onChange={(e) => seek(Number(e.target.value))}
          aria-label={t('podcasts.playerSeek')}
          className="h-1.5 flex-1 cursor-pointer accent-primary"
        />
        <span className="w-12">{formatTime(duration)}</span>
      </div>

      {/* ±10秒 / 速度 / 音量 */}
      <div className="flex items-center justify-center gap-2">
        <Button
          variant="ghost"
          size="sm"
          className="h-10 w-10 p-0"
          aria-label={t('podcasts.playerBack10')}
          onClick={() => skip(-SKIP_SECONDS)}
        >
          <span className="relative inline-flex">
            <RotateCcw className="h-5 w-5" />
            <span className="absolute inset-0 flex items-center justify-center text-[8px] font-bold">
              10
            </span>
          </span>
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-10 min-w-16 font-mono tabular-nums"
          aria-label={t('podcasts.playerSpeed')}
          onClick={() => setPlaybackRate(nextRate(playbackRate))}
        >
          {playbackRate.toFixed(2).replace(/0$/, '')}x
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="h-10 w-10 p-0"
          aria-label={t('podcasts.playerForward10')}
          onClick={() => skip(SKIP_SECONDS)}
        >
          <span className="relative inline-flex">
            <RotateCw className="h-5 w-5" />
            <span className="absolute inset-0 flex items-center justify-center text-[8px] font-bold">
              10
            </span>
          </span>
        </Button>
        {/* 音量: iOS はOS側制御のためスマホでは非表示 */}
        <div className="hidden items-center gap-1.5 sm:flex">
          <Volume2 className="h-4 w-4 text-muted-foreground" />
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={volume}
            onChange={(e) => setVolume(Number(e.target.value))}
            aria-label={t('podcasts.playerVolume')}
            className="h-1.5 w-24 cursor-pointer accent-primary"
          />
        </div>
      </div>
    </div>
  )
}
