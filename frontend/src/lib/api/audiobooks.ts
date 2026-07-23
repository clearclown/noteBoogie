import {
  Audiobook,
  AudiobookDetail,
  BookFigure,
  GenerateAudiobookRequest,
  GenerateAudiobookResponse,
} from '@/lib/types/audiobooks'

/**
 * Client for the Book Navigator gateway (Rust, reinhardt-web).
 *
 * Uses plain fetch instead of the shared axios client: the gateway is a
 * separate service with its own base URL and no auth middleware.
 */
export function getGatewayUrl(): string {
  return process.env.NEXT_PUBLIC_GATEWAY_URL || 'http://localhost:8088'
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${getGatewayUrl()}${path}`)
  if (!response.ok) {
    throw new Error(`Gateway request failed (${response.status}): ${path}`)
  }
  return (await response.json()) as T
}

export const audiobooksApi = {
  list: () => getJson<Audiobook[]>('/audiobooks'),

  get: (audiobookId: string) =>
    getJson<AudiobookDetail>(`/audiobooks/${encodeURIComponent(audiobookId)}`),

  listFigures: (audiobookId: string) =>
    getJson<BookFigure[]>(`/audiobooks/${encodeURIComponent(audiobookId)}/figures`),

  delete: async (audiobookId: string) => {
    const response = await fetch(
      `${getGatewayUrl()}/audiobooks/${encodeURIComponent(audiobookId)}`,
      { method: 'DELETE' }
    )
    if (!response.ok) {
      throw new Error(`Gateway delete failed (${response.status})`)
    }
  },

  generate: async (payload: GenerateAudiobookRequest) => {
    const response = await fetch(`${getGatewayUrl()}/audiobooks/generate`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!response.ok) {
      throw new Error(`Gateway generate failed (${response.status})`)
    }
    return (await response.json()) as GenerateAudiobookResponse
  },

  figureImageUrl: (figureId: string) =>
    `${getGatewayUrl()}/figures/${encodeURIComponent(figureId)}/image`,
}
