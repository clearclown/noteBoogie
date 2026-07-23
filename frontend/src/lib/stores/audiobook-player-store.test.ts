import { beforeEach, describe, expect, it } from 'vitest'

import { useAudiobookPlayerStore } from './audiobook-player-store'

describe('audiobook-player-store', () => {
  beforeEach(() => {
    useAudiobookPlayerStore.setState({
      autoAdvance: true,
      lastAudiobookId: null,
      lastChapterIndex: null,
    })
    window.localStorage.removeItem('audiobook-player-storage')
  })

  it('defaults to auto-advance on', () => {
    expect(useAudiobookPlayerStore.getState().autoAdvance).toBe(true)
  })

  it('toggles auto-advance and persists it', () => {
    useAudiobookPlayerStore.getState().setAutoAdvance(false)
    expect(useAudiobookPlayerStore.getState().autoAdvance).toBe(false)

    const persisted = JSON.parse(
      window.localStorage.getItem('audiobook-player-storage') ?? '{}'
    )
    expect(persisted.state.autoAdvance).toBe(false)
  })

  it('remembers the listening position', () => {
    useAudiobookPlayerStore.getState().setPosition('audiobook:a', 3)
    const state = useAudiobookPlayerStore.getState()
    expect(state.lastAudiobookId).toBe('audiobook:a')
    expect(state.lastChapterIndex).toBe(3)

    useAudiobookPlayerStore.getState().setPosition(null, null)
    expect(useAudiobookPlayerStore.getState().lastAudiobookId).toBeNull()
  })
})
