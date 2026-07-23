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
  if (process.env.NEXT_PUBLIC_GATEWAY_URL) {
    return process.env.NEXT_PUBLIC_GATEWAY_URL
  }
  // Runtime derivation from the page host: with the containerized stack the
  // browser may be a Tailscale peer, where a baked-in "localhost" would point
  // at the CLIENT machine. Same host, port 8088 (compose default).
  if (typeof window !== 'undefined' && window.location?.hostname) {
    return `${window.location.protocol}//${window.location.hostname}:8088`
  }
  return 'http://localhost:8088'
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

  retryChapter: async (chapterId: string) => {
    const response = await fetch(
      `${getGatewayUrl()}/chapters/${encodeURIComponent(chapterId)}/retry`,
      { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' }
    )
    if (!response.ok) {
      throw new Error(`Gateway retry failed (${response.status})`)
    }
  },

  figureImageUrl: (figureId: string) =>
    `${getGatewayUrl()}/figures/${encodeURIComponent(figureId)}/image`,
}
