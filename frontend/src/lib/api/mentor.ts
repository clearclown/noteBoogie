import { apiClient } from './client'

export interface MentorSourceRef {
  id: string
  title: string
}

export interface MentorConsultResponse {
  answer: string
  sources: MentorSourceRef[]
  message_id: string | null
}

export interface MentorMessage {
  id: string
  role: 'user' | 'mentor'
  content: string
  sources: string[] | null
  created: string | null
}

export interface MentorMemory {
  id: string
  question: string
  gist: string
  sources: string[] | null
  created: string | null
}

export interface MentorWeightEntry {
  source_id: string
  title: string
  weight: number
  chapter_weights: Record<string, number> | null
  auto_factor: number
  chapters: string[]
}

export interface MentorWeightUpdate {
  weight: number
  chapter_weights?: Record<string, number> | null
}

export interface SlideIssue {
  id: string | null
  page: number
  text: string
  fix: string | null
  rule: string | null
  applicable: boolean
}

export interface SlideAxis {
  key: string
  score: number
  issues: SlideIssue[]
  passed: boolean
}

export interface SlideReview {
  id: string | null
  filename: string
  kind: 'image' | 'pdf' | 'pptx'
  page_count: number
  overall: number
  passed: boolean
  threshold: number
  axes: SlideAxis[]
  summary: string | null
  top_fix: string | null
  citations: MentorSourceRef[]
  created: string | null
}

export const mentorApi = {
  consult: async (message: string): Promise<MentorConsultResponse> => {
    const response = await apiClient.post('/mentor/consult', { message })
    return response.data
  },

  getMessages: async (limit = 50): Promise<MentorMessage[]> => {
    const response = await apiClient.get('/mentor/messages', { params: { limit } })
    return response.data
  },

  getMemories: async (limit = 20): Promise<MentorMemory[]> => {
    const response = await apiClient.get('/mentor/memories', { params: { limit } })
    return response.data
  },

  deleteMemory: async (memoryId: string): Promise<void> => {
    await apiClient.delete(`/mentor/memories/${encodeURIComponent(memoryId)}`)
  },

  // 師匠回答のTTS音声（mp3 blob）。サーバー側でキャッシュされる
  speak: async (messageId: string): Promise<Blob> => {
    const response = await apiClient.post(
      `/mentor/speak/${encodeURIComponent(messageId)}`,
      null,
      { responseType: 'blob' }
    )
    return response.data
  },

  getWeights: async (): Promise<MentorWeightEntry[]> => {
    const response = await apiClient.get('/mentor/weights')
    return response.data
  },

  updateWeight: async (
    sourceId: string,
    update: MentorWeightUpdate
  ): Promise<MentorWeightEntry> => {
    const response = await apiClient.put(
      `/mentor/weights/${encodeURIComponent(sourceId)}`,
      update
    )
    return response.data
  },

  getPersona: async (): Promise<{ persona: string; is_default: boolean }> => {
    const response = await apiClient.get('/mentor/persona')
    return response.data
  },

  updatePersona: async (
    persona: string
  ): Promise<{ persona: string; is_default: boolean }> => {
    const response = await apiClient.put('/mentor/persona', { persona })
    return response.data
  },

  reviewSlides: async (file: File): Promise<SlideReview> => {
    const form = new FormData()
    form.append('file', file)
    const response = await apiClient.post('/mentor/slide-review', form)
    return response.data
  },

  listSlideReviews: async (limit = 20): Promise<SlideReview[]> => {
    const response = await apiClient.get('/mentor/slide-reviews', {
      params: { limit },
    })
    return response.data
  },

  // 選択した指摘を適用した _coached.pptx を blob で受け取る
  applySlideFixes: async (reviewId: string, issueIds: string[]): Promise<Blob> => {
    const response = await apiClient.post(
      `/mentor/slide-review/${encodeURIComponent(reviewId)}/apply`,
      { issue_ids: issueIds },
      { responseType: 'blob' }
    )
    return response.data
  },
}
