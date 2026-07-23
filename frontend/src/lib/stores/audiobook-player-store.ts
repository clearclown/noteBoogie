import { create } from 'zustand'
import { persist } from 'zustand/middleware'

/**
 * Player preferences + last position for the audiobooks tab.
 *
 * Persisted so auto-advance and the listening position survive tab switches
 * and reloads (the <audio> element itself lives in AudiobooksTab and stops on
 * unmount — a known deviation from a fully persistent player).
 */
interface AudiobookPlayerState {
  autoAdvance: boolean
  lastAudiobookId: string | null
  lastChapterIndex: number | null
  /** 再生速度（0.5〜2.0、音楽プレーヤ同様に永続化） */
  playbackRate: number
  /** 音量 0〜1（iOS はOS音量が優先されJSからは変更不可） */
  volume: number
  setAutoAdvance: (value: boolean) => void
  setPosition: (audiobookId: string | null, chapterIndex: number | null) => void
  setPlaybackRate: (rate: number) => void
  setVolume: (volume: number) => void
}

export const useAudiobookPlayerStore = create<AudiobookPlayerState>()(
  persist(
    (set) => ({
      autoAdvance: true,
      lastAudiobookId: null,
      lastChapterIndex: null,
      playbackRate: 1.0,
      volume: 1.0,
      setAutoAdvance: (value) => set({ autoAdvance: value }),
      setPosition: (audiobookId, chapterIndex) =>
        set({ lastAudiobookId: audiobookId, lastChapterIndex: chapterIndex }),
      setPlaybackRate: (rate) =>
        set({ playbackRate: Math.min(2.0, Math.max(0.5, rate)) }),
      setVolume: (volume) => set({ volume: Math.min(1.0, Math.max(0.0, volume)) }),
    }),
    { name: 'audiobook-player-storage' }
  )
)
