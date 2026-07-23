import { expect, Page, test } from '@playwright/test'

/**
 * Audiobooks tab E2E: real Next.js app + real browser, backend/gateway
 * mocked at the network layer. Covers the full user path the unit tests
 * can only approximate: routing, tab switching, cross-origin gateway
 * fetches (CORS in a real browser), tracklist interaction, and the
 * figure gallery.
 */

const API = 'http://localhost:5055'
const GATEWAY = 'http://localhost:8088'

const AUDIOBOOK = {
  id: 'audiobook:e2e',
  name: 'コンサル頭のつくり方',
  source_id: 'source:s1',
  briefing: null,
  chapter_count: 2,
}

const DETAIL = {
  ...AUDIOBOOK,
  chapters: [
    {
      id: 'episode:c0',
      name: '第1章:序',
      chapter_index: 0,
      chapter_title: '序',
      audio_file: 'episodes/c0/a.mp3',
    },
    {
      id: 'episode:c1',
      name: '第2章:本論',
      chapter_index: 1,
      chapter_title: '本論',
      audio_file: null,
    },
  ],
}

const FIGURES = [
  {
    id: 'book_figure:f1',
    page: 3,
    chapter_index: 0,
    kind: 'figure',
    caption: 'イシューツリーの図',
  },
]

// 1x1 transparent PNG
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64'
)

async function mockBackends(page: Page) {
  // Catch-alls FIRST (Playwright matches the most recently registered route
  // first, so the specific mocks below take precedence). Locally a real API
  // may be running and quietly answering unmocked requests — on CI nothing
  // else runs, and a single unmocked hanging request blocks the whole UI
  // behind the ConnectionGuard overlay. Keep the suite hermetic.
  await page.route(`${API}/**`, (route) => {
    console.log('[unmocked API]', route.request().method(), route.request().url())
    return route.fulfill({ json: {} })
  })
  await page.route(`${GATEWAY}/**`, (route) => {
    console.log('[unmocked GW]', route.request().method(), route.request().url())
    return route.fulfill({ json: [] })
  })

  // Next.js runtime config -> point the app at the mocked API origin.
  await page.route('**/config', (route) =>
    route.fulfill({ json: { apiUrl: API } })
  )
  // FastAPI surface the app touches on load.
  await page.route(`${API}/api/auth/status`, (route) =>
    route.fulfill({ json: { auth_enabled: false } })
  )
  await page.route(`${API}/api/health`, (route) =>
    route.fulfill({ json: { status: 'healthy' } })
  )
  await page.route(`${API}/api/notebooks*`, (route) =>
    route.fulfill({ json: [] })
  )
  await page.route(`${API}/api/episode-profiles`, (route) =>
    route.fulfill({ json: [] })
  )
  await page.route(`${API}/api/speaker-profiles`, (route) =>
    route.fulfill({ json: [] })
  )
  await page.route(`${API}/api/podcasts/episodes`, (route) =>
    route.fulfill({ json: [] })
  )
  await page.route(`${API}/api/models*`, (route) => route.fulfill({ json: [] }))
  // Array-shaped endpoints: the catch-all's `{}` would crash `.map()` calls
  // and throw the page into an ErrorBoundary remount loop.
  await page.route(`${API}/api/languages`, (route) => route.fulfill({ json: [] }))
  await page.route(`${API}/api/sources*`, (route) => route.fulfill({ json: [] }))
  await page.route(`${API}/api/transformations*`, (route) =>
    route.fulfill({ json: [] })
  )
  await page.route(`${API}/api/notes*`, (route) => route.fulfill({ json: [] }))
  await page.route(`${API}/api/recently-viewed*`, (route) =>
    route.fulfill({ json: [] })
  )
  await page.route(`${API}/api/credentials/status`, (route) =>
    route.fulfill({
      json: { configured: {}, source: {}, encryption_configured: false },
    })
  )
  await page.route(`${API}/api/credentials/env-status`, (route) =>
    route.fulfill({ json: {} })
  )
  await page.route(`${API}/api/settings*`, (route) => route.fulfill({ json: {} }))
  await page.route(`${API}/api/podcasts/episodes/episode%3Ac0/audio`, (route) =>
    route.fulfill({ contentType: 'audio/mpeg', body: Buffer.from('fakemp3') })
  )
  // Gateway surface.
  await page.route(`${GATEWAY}/audiobooks`, (route) =>
    route.fulfill({ json: [AUDIOBOOK] })
  )
  await page.route(`${GATEWAY}/audiobooks/audiobook%3Ae2e`, (route) =>
    route.fulfill({ json: DETAIL })
  )
  await page.route(`${GATEWAY}/audiobooks/audiobook%3Ae2e/figures`, (route) =>
    route.fulfill({ json: FIGURES })
  )
  await page.route(`${GATEWAY}/figures/book_figure%3Af1/image`, (route) =>
    route.fulfill({ contentType: 'image/png', body: PNG })
  )
}

test.beforeEach(async ({ page }) => {
  // Pre-seed the persisted auth state: these tests target the audiobooks
  // feature, not the login flow, and the auth guard's client-side races
  // (bounce to /login and back) otherwise flake the suite.
  await page.addInitScript(() => {
    window.localStorage.setItem(
      'auth-storage',
      JSON.stringify({
        state: { token: 'not-required', isAuthenticated: true },
        version: 0,
      })
    )
  })
  // Surface page crashes in the test output — an ErrorBoundary screen is
  // otherwise just a silent timeout.
  page.on('pageerror', (error) => console.log('[pageerror]', error.message))
  page.on('console', (message) => {
    if (message.type() === 'error') {
      console.log('[console.error]', message.text().slice(0, 300))
    }
  })
  await mockBackends(page)
})

test('podcasts page exposes the audiobooks tab', async ({ page }) => {
  await page.goto('/podcasts')
  await expect(
    page.getByRole('tab', { name: /オーディオブック|Audiobooks/ })
  ).toBeVisible()
})

test('audiobook list -> tracklist -> figure gallery flow', async ({ page }) => {
  await page.goto('/podcasts')
  await page.getByRole('tab', { name: /オーディオブック|Audiobooks/ }).click()

  // List card with the book name and chapter count badge.
  await expect(page.getByText('コンサル頭のつくり方')).toBeVisible()

  // Open the detail view.
  await page.getByText('コンサル頭のつくり方').click()
  await expect(page.getByText('第1章:序')).toBeVisible()

  // Pending chapter is rendered disabled (still generating).
  const pending = page.getByRole('button', { name: /第2章:本論/ })
  await expect(pending).toBeDisabled()

  // Figure gallery shows the vision caption and loads the image from the
  // gateway (a cross-origin fetch in a real browser).
  await expect(page.getByText('イシューツリーの図')).toBeVisible()
  const img = page.locator('img[alt="イシューツリーの図"]')
  await expect(img).toBeVisible()

  // Selecting the ready chapter requests the protected audio endpoint.
  const audioRequest = page.waitForRequest((req) =>
    req.url().includes('/api/podcasts/episodes/episode%3Ac0/audio')
  )
  await page.getByRole('button', { name: /第1章:序/ }).click()
  await audioRequest
  await expect(page.locator('audio')).toBeVisible()
})

test('empty state renders when no audiobooks exist', async ({ page }) => {
  await page.route(`${GATEWAY}/audiobooks`, (route) => route.fulfill({ json: [] }))
  await page.goto('/podcasts')
  await page.getByRole('tab', { name: /オーディオブック|Audiobooks/ }).click()
  await expect(
    page.getByText(/オーディオブックはまだありません|No audiobooks yet/)
  ).toBeVisible()
})
