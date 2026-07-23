import { fireEvent, render, screen } from '@testing-library/react'
import { createRef } from 'react'
import { beforeEach, describe, expect, it } from 'vitest'

import {
  AudiobookPlayerControls,
  formatTime,
  nextRate,
  PLAYBACK_RATES,
} from './AudiobookPlayerControls'
import { useAudiobookPlayerStore } from '@/lib/stores/audiobook-player-store'

function makeAudio(): HTMLAudioElement {
  const el = document.createElement('audio')
  // jsdom は duration を実装しないため上書き
  Object.defineProperty(el, 'duration', { value: 300, configurable: true })
  el.currentTime = 60
  return el
}

beforeEach(() => {
  useAudiobookPlayerStore.setState({ playbackRate: 1.0, volume: 1.0 })
})

describe('formatTime / nextRate', () => {
  it('formats mm:ss and h:mm:ss', () => {
    expect(formatTime(0)).toBe('0:00')
    expect(formatTime(65)).toBe('1:05')
    expect(formatTime(3671)).toBe('1:01:11')
    expect(formatTime(NaN)).toBe('0:00')
  })

  it('cycles through the standard music-player rates', () => {
    expect(nextRate(1.0)).toBe(1.25)
    expect(nextRate(2.0)).toBe(0.75) // 端で先頭へ戻る
    expect(PLAYBACK_RATES).toContain(1.5)
  })
})

describe('AudiobookPlayerControls', () => {
  it('renders nothing without a source', () => {
    const ref = createRef<HTMLAudioElement | null>()
    const { container } = render(
      <AudiobookPlayerControls audioRef={ref} audioSrc={null} />
    )
    expect(container.firstChild).toBeNull()
  })

  it('applies the persisted playback rate and cycles it on tap', () => {
    const ref = { current: makeAudio() }
    useAudiobookPlayerStore.setState({ playbackRate: 1.5 })
    render(<AudiobookPlayerControls audioRef={ref} audioSrc="blob:x" />)

    expect(ref.current.playbackRate).toBe(1.5)
    fireEvent.click(screen.getByRole('button', { name: 'podcasts.playerSpeed' }))
    expect(useAudiobookPlayerStore.getState().playbackRate).toBe(1.75)
    expect(ref.current.playbackRate).toBe(1.75)
  })

  it('skips ±10 seconds with clamping', () => {
    const ref = { current: makeAudio() }
    render(<AudiobookPlayerControls audioRef={ref} audioSrc="blob:x" />)

    fireEvent.click(screen.getByRole('button', { name: 'podcasts.playerForward10' }))
    expect(ref.current.currentTime).toBe(70)
    fireEvent.click(screen.getByRole('button', { name: 'podcasts.playerBack10' }))
    expect(ref.current.currentTime).toBe(60)

    ref.current.currentTime = 3
    fireEvent.click(screen.getByRole('button', { name: 'podcasts.playerBack10' }))
    expect(ref.current.currentTime).toBe(0) // 0未満に行かない
  })

  it('seeks via the range input and shows duration', () => {
    const ref = { current: makeAudio() }
    render(<AudiobookPlayerControls audioRef={ref} audioSrc="blob:x" />)

    expect(screen.getByText('5:00')).toBeInTheDocument() // duration 300s
    fireEvent.change(screen.getByRole('slider', { name: 'podcasts.playerSeek' }), {
      target: { value: '120' },
    })
    expect(ref.current.currentTime).toBe(120)
    expect(screen.getByText('2:00')).toBeInTheDocument()
  })

  it('persists volume changes to the store', () => {
    const ref = { current: makeAudio() }
    render(<AudiobookPlayerControls audioRef={ref} audioSrc="blob:x" />)
    fireEvent.change(screen.getByRole('slider', { name: 'podcasts.playerVolume' }), {
      target: { value: '0.4' },
    })
    expect(useAudiobookPlayerStore.getState().volume).toBe(0.4)
    expect(ref.current.volume).toBe(0.4)
  })
})
