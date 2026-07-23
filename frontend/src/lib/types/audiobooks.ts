/**
 * Book Navigator audiobooks (personal fork feature).
 *
 * Served by the Rust gateway (reinhardt-web, :8088), NOT the FastAPI backend.
 * Chapter audio itself is streamed through the existing protected API
 * endpoint `/api/podcasts/episodes/{id}/audio` because chapters are regular
 * `episode` records.
 */

export interface AudiobookChapter {
  id: string | null
  name: string | null
  chapter_index: number | null
  chapter_title: string | null
  /** Relative audio path; null until the chapter has been generated. */
  audio_file: string | null
  /** Permanent generation failure message (null while pending/succeeded). */
  generation_error?: string | null
}

export interface Audiobook {
  id: string | null
  name: string | null
  source_id: string | null
  briefing: string | null
  chapter_count: number | null
  created?: string | null
}

export interface GenerateAudiobookRequest {
  audiobook_name: string
  source_id?: string
  content?: string
  max_chapters?: number
  briefing_suffix?: string
}

export interface GenerateAudiobookResponse {
  audiobook_id: string
  audiobook_name: string
  chapter_count: number
  status: string
}

export interface AudiobookDetail extends Audiobook {
  chapters: AudiobookChapter[]
}

export interface BookFigure {
  id: string | null
  page: number | null
  chapter_index: number | null
  kind: string | null
  caption: string | null
}
