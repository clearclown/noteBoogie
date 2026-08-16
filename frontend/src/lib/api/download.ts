import { getApiUrl } from '@/lib/config'

/**
 * 選択した章（エピソード）を1つのZIPでまとめてダウンロードする。
 *
 * サーバ側(/api/podcasts/download)でZIP化するため、スマホでも「1ファイルDL」で
 * 完結する（クライアントで数百MBのblobを抱えない）。認証トークンがあれば付与する。
 */
export async function downloadEpisodesZip(
  episodeIds: string[],
  zipName: string
): Promise<void> {
  if (episodeIds.length === 0) return
  const base = await getApiUrl()

  let token: string | undefined
  if (typeof window !== 'undefined') {
    const raw = window.localStorage.getItem('auth-storage')
    if (raw) {
      try {
        token = JSON.parse(raw)?.state?.token
      } catch {
        // トークン解析失敗は無視して未認証で続行
      }
    }
  }

  const response = await fetch(`${base}/api/podcasts/download`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ episode_ids: episodeIds, zip_name: zipName }),
  })
  if (!response.ok) {
    throw new Error(`Download failed (${response.status})`)
  }

  const blob = await response.blob()
  triggerBlobDownload(blob, `${sanitize(zipName) || 'audiobook'}.zip`)
}

/** 単一のBlobをブラウザのダウンロードとして保存する（スマホ含む）。 */
export function triggerBlobDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  // iOS Safari は click 直後の revoke で失敗することがあるため少し遅らせる
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

function sanitize(name: string): string {
  return (name || '').replace(/[\\/:*?"<>|]/g, '').trim()
}
