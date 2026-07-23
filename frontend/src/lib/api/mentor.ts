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
}
