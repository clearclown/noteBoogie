import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { audiobooksApi, getGatewayUrl } from './audiobooks'

describe('getGatewayUrl', () => {
  it('falls back to the local gateway when the env var is unset', () => {
    expect(getGatewayUrl()).toBe('http://localhost:8088')
  })
})

describe('getGatewayUrl with env override', () => {
  it('prefers NEXT_PUBLIC_GATEWAY_URL when set', () => {
    vi.stubEnv('NEXT_PUBLIC_GATEWAY_URL', 'http://gw.example:9999')
    expect(getGatewayUrl()).toBe('http://gw.example:9999')
    vi.unstubAllEnvs()
  })
})

describe('audiobooksApi', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const okJson = (data: unknown) => ({
    ok: true,
    status: 200,
    json: async () => data,
  })

  it('lists audiobooks from the gateway', async () => {
    fetchMock.mockResolvedValue(okJson([{ id: 'audiobook:a' }]))
    const result = await audiobooksApi.list()
    expect(fetchMock).toHaveBeenCalledWith('http://localhost:8088/audiobooks')
    expect(result[0].id).toBe('audiobook:a')
  })

  it('URL-encodes record ids in paths', async () => {
    fetchMock.mockResolvedValue(okJson({ id: 'audiobook:x', chapters: [] }))
    await audiobooksApi.get('audiobook:x')
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8088/audiobooks/audiobook%3Ax'
    )
    await audiobooksApi.listFigures('audiobook:x')
    expect(fetchMock).toHaveBeenLastCalledWith(
      'http://localhost:8088/audiobooks/audiobook%3Ax/figures'
    )
  })

  it('builds figure image URLs without fetching', () => {
    expect(audiobooksApi.figureImageUrl('book_figure:f1')).toBe(
      'http://localhost:8088/figures/book_figure%3Af1/image'
    )
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('throws with status on a failed GET', async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 500, json: async () => ({}) })
    await expect(audiobooksApi.list()).rejects.toThrow('500')
  })

  it('deletes via the DELETE method and throws on failure', async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 200 })
    await audiobooksApi.delete('audiobook:z')
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8088/audiobooks/audiobook%3Az',
      { method: 'DELETE' }
    )
    fetchMock.mockResolvedValue({ ok: false, status: 404 })
    await expect(audiobooksApi.delete('audiobook:z')).rejects.toThrow('404')
  })
})
