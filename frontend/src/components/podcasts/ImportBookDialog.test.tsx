import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ImportBookDialog } from './ImportBookDialog'

vi.mock('@/lib/api/client', () => ({
  default: { post: vi.fn(), get: vi.fn() },
  apiClient: { post: vi.fn(), get: vi.fn() },
}))

import apiClient from '@/lib/api/client'

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ImportBookDialog', () => {
  it('disables start until a PDF is chosen, then submits multipart', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      data: { job_id: 'command:j1', status: 'submitted' },
    } as never)
    render(<ImportBookDialog open={true} onOpenChange={vi.fn()} />, { wrapper })

    const start = screen.getByRole('button', { name: /importBookStart/ })
    expect(start).toBeDisabled()

    const file = new File([new Uint8Array([1])], '新しい本.pdf', {
      type: 'application/pdf',
    })
    fireEvent.change(screen.getByTestId('import-book-file'), {
      target: { files: [file] },
    })
    fireEvent.change(screen.getByLabelText(/importBookName/), {
      target: { value: '新しい本' },
    })
    expect(start).toBeEnabled()

    fireEvent.click(start)
    await waitFor(() => expect(apiClient.post).toHaveBeenCalled())
    const [url, form] = vi.mocked(apiClient.post).mock.calls[0]
    expect(url).toBe('/books/import')
    expect((form as FormData).get('title')).toBe('新しい本')
    expect(((form as FormData).get('file') as File).name).toBe('新しい本.pdf')
    // 実行中表示に遷移
    await screen.findByText('podcasts.importBookRunning')
  })

  it('shows a failure state when the upload errors', async () => {
    vi.mocked(apiClient.post).mockRejectedValue(new Error('boom'))
    render(<ImportBookDialog open={true} onOpenChange={vi.fn()} />, { wrapper })
    fireEvent.change(screen.getByTestId('import-book-file'), {
      target: {
        files: [new File([new Uint8Array([1])], 'b.pdf', { type: 'application/pdf' })],
      },
    })
    fireEvent.click(screen.getByRole('button', { name: /importBookStart/ }))
    await screen.findByText('boom')
  })
})
