import { devices, expect, Page, test } from '@playwright/test'

/**
 * スマホ（iPhone 13 相当 390x844）でのレスポンシブ検証。
 * Tailscale からスマホでアクセスする利用形態を想定し、主要ページが
 * 横スクロールなしで収まり、操作要素が見えることを保証する。
 */

test.use({ viewport: devices['iPhone 13'].viewport })

const API = 'http://localhost:5055'
const GATEWAY = 'http://localhost:8088'

async function mockBackends(page: Page) {
  await page.route(`${API}/**`, (route) => route.fulfill({ json: {} }))
  await page.route(`${GATEWAY}/**`, (route) => route.fulfill({ json: [] }))
  await page.route('**/config', (route) => route.fulfill({ json: { apiUrl: API } }))
  await page.route(`${API}/api/auth/status`, (route) =>
    route.fulfill({ json: { auth_enabled: false } })
  )
  await page.route(`${API}/api/health`, (route) =>
    route.fulfill({ json: { status: 'healthy' } })
  )
  for (const path of [
    'notebooks*', 'models*', 'languages', 'sources*', 'transformations*',
    'notes*', 'recently-viewed*', 'episode-profiles', 'speaker-profiles',
    'podcasts/episodes', 'mentor/messages*', 'mentor/memories*',
  ]) {
    await page.route(`${API}/api/${path}`, (route) => route.fulfill({ json: [] }))
  }
  await page.route(`${API}/api/mentor/personas`, (route) =>
    route.fulfill({
      json: [{ name: 'default', persona: 'コンサルの師匠です。', active: true }],
    })
  )
  await page.route(`${API}/api/mentor/weights`, (route) =>
    route.fulfill({
      json: [
        {
          source_id: 'source:s1',
          title: 'とても長いタイトルの本でもはみ出さないことを確認する',
          weight: 1.0,
          chapter_weights: null,
          auto_factor: 1.12,
          chapters: ['第1章', '第2章'],
        },
      ],
    })
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
  await page.route(`${GATEWAY}/audiobooks`, (route) =>
    route.fulfill({
      json: [
        {
          id: 'audiobook:m',
          name: 'コンサル頭のつくり方（全量）',
          source_id: 'source:s1',
          briefing: null,
          chapter_count: 13,
        },
      ],
    })
  )
}

async function assertNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => {
    const el = document.scrollingElement ?? document.documentElement
    return el.scrollWidth - el.clientWidth
  })
  expect(overflow, 'page must not scroll horizontally on phones').toBeLessThanOrEqual(1)
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      'auth-storage',
      JSON.stringify({
        state: { token: 'not-required', isAuthenticated: true },
        version: 0,
      })
    )
  })
  await mockBackends(page)
})

test('mentor page fits a phone: chat, tabs, weights', async ({ page }) => {
  await page.goto('/mentor')
  await expect(page.getByRole('textbox')).toBeVisible()
  await assertNoHorizontalOverflow(page)

  await page.getByRole('tab', { name: /学習の傾斜|Learning weights/ }).click()
  await expect(page.getByTestId('mentor-weight-row')).toBeVisible()
  await assertNoHorizontalOverflow(page)
})

test('podcasts audiobooks tab fits a phone', async ({ page }) => {
  await page.goto('/podcasts')
  await page.getByRole('tab', { name: /オーディオブック|Audiobooks/ }).click()
  await expect(page.getByText('コンサル頭のつくり方（全量）')).toBeVisible()
  await assertNoHorizontalOverflow(page)
})

test('sidebar auto-collapses to an icon rail on phones', async ({ page }) => {
  await page.goto('/mentor')
  const sidebar = page.locator('.app-sidebar')
  await expect(sidebar).toBeVisible()
  // transition-all 300ms で 256→64px へ収縮するため、収束を待って測る
  await expect
    .poll(async () => (await sidebar.boundingBox())?.width ?? 999, {
      message: 'sidebar must settle to an icon rail (64px) on phones',
    })
    .toBeLessThanOrEqual(72)
})
