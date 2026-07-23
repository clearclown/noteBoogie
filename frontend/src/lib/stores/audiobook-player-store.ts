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
  setAutoAdvance: (value: boolean) => void
  setPosition: (audiobookId: string | null, chapterIndex: number | null) => void
}

export const useAudiobookPlayerStore = create<AudiobookPlayerState>()(
  persist(
    (set) => ({
      autoAdvance: true,
      lastAudiobookId: null,
      lastChapterIndex: null,
      setAutoAdvance: (value) => set({ autoAdvance: value }),
      setPosition: (audiobookId, chapterIndex) =>
        set({ lastAudiobookId: audiobookId, lastChapterIndex: chapterIndex }),
    }),
    { name: 'audiobook-player-storage' }
  )
)
