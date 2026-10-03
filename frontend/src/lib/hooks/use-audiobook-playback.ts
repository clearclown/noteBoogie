'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

import { podcastsApi } from '@/lib/api/podcasts'
import { useAudiobookPlayerStore } from '@/lib/stores/audiobook-player-store'
import type { AudiobookChapter } from '@/lib/types/audiobooks'

/** Local player lifecycle, including cancelled loads and per-book resume positions. */
export function useAudiobookPlayback(audiobookId: string, chapters: AudiobookChapter[]) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [chapterId, setChapterId] = useState<string | null>(null)
  const [audioSrc, setAudioSrc] = useState<string>()
  const [audioError, setAudioError] = useState(false)
  const [playing, setPlaying] = useState(false)
  const resumeAt = useRef(0)
  const metadataReady = useRef(false)
  const shouldPlay = useRef(false)
  const chapter = chapters.find((item) => item.id === chapterId)
  const audioFile = chapter?.audio_file
  const saveProgress = useAudiobookPlayerStore((s) => s.saveProgress)

  const remember = useCallback(() => {
    const audio = audioRef.current
    if (audio && chapterId && metadataReady.current) {
      saveProgress(audiobookId, chapterId, audio.ended ? 0 : audio.currentTime)
    }
  }, [audiobookId, chapterId, saveProgress])

  const pause = () => {
    shouldPlay.current = false
    audioRef.current?.pause()
    remember()
    setPlaying(false)
  }

  const play = () => {
    shouldPlay.current = true
    if (metadataReady.current) {
      void audioRef.current?.play().catch(() => setPlaying(false))
    }
  }

  const selectChapter = (id: string, seconds = 0) => {
    remember()
    resumeAt.current = seconds
    shouldPlay.current = true
    if (id === chapterId && audioRef.current && metadataReady.current) {
      audioRef.current.currentTime = seconds
      play()
    } else {
      metadataReady.current = false
      setChapterId(id)
    }
  }

  useEffect(() => {
    setAudioSrc(undefined)
    setAudioError(false)
    setPlaying(false)
    metadataReady.current = false
    if (!chapterId || !audioFile) return
    const controller = new AbortController()
    let objectUrl: string | undefined
    void podcastsApi.getChapterAudio(chapterId, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted) return
        objectUrl = URL.createObjectURL(blob)
        setAudioSrc(objectUrl)
      })
      .catch(() => { if (!controller.signal.aborted) setAudioError(true) })
    return () => {
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [chapterId, audioFile])

  useEffect(() => {
    // Capture this element: by cleanup time React may have cleared audioRef.
    const audio = audioRef.current
    const flush = () => {
      if (audio && chapterId && metadataReady.current) {
        saveProgress(audiobookId, chapterId, audio.ended ? 0 : audio.currentTime)
      }
    }
    window.addEventListener('pagehide', flush)
    document.addEventListener('visibilitychange', flush)
    return () => {
      flush()
      window.removeEventListener('pagehide', flush)
      document.removeEventListener('visibilitychange', flush)
    }
  }, [audiobookId, chapterId, audioSrc, saveProgress])

  const onLoadedMetadata = () => {
    const audio = audioRef.current
    if (!audio) return
    if (!metadataReady.current) {
      const saved = resumeAt.current
      audio.currentTime = Number.isFinite(saved) && saved >= 0 &&
        (!Number.isFinite(audio.duration) || saved < audio.duration) ? saved : 0
      metadataReady.current = true
      remember()
      if (shouldPlay.current) play()
    }
  }

  return {
    audioRef, audioSrc, audioError, playing, chapterId,
    selectChapter, pause, play, remember, onLoadedMetadata,
    onPlay: () => setPlaying(true),
    onPause: () => { setPlaying(false); remember() },
  }
}
