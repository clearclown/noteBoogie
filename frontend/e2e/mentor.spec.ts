import { expect, Page, test } from '@playwright/test'

/**
 * /mentor page E2E (hermetic): consult flow — send a question, see the
 * optimistic user bubble, then the mentor answer with referenced-book
 * chips — and the memory panel reflecting the stored consultation.
 * Backend is mocked at the network layer (same pattern as audiobooks).
 */

const API = 'http://localhost:5055'
const GATEWAY = 'http://localhost:8088'

async function mockBackends(page: Page) {
  // Catch-alls first; specific mocks below take precedence.
  await page.route(`${API}/**`, (route) => {
    console.log('[unmocked API]', route.request().method(), route.request().url())
    return route.fulfill({ json: {} })
  })
  await page.route(`${GATEWAY}/**`, (route) => route.fulfill({ json: [] }))

  await page.route('**/config', (route) => route.fulfill({ json: { apiUrl: API } }))
  await page.route(`${API}/api/auth/status`, (route) =>
    route.fulfill({ json: { auth_enabled: false } })
  )
  await page.route(`${API}/api/health`, (route) =>
    route.fulfill({ json: { status: 'healthy' } })
  )
  // Array-shaped endpoints the shell touches (catch-all `{}` breaks `.map()`).
  for (const path of [
    'notebooks*', 'models*', 'languages', 'sources*', 'transformations*',
    'notes*', 'recently-viewed*', 'episode-profiles', 'speaker-profiles',
    'podcasts/episodes',
  ]) {
    await page.route(`${API}/api/${path}`, (route) => route.fulfill({ json: [] }))
  }
  await page.route(`${API}/api/credentials/status`, (route) =>
    route.fulfill({
      json: { configured: {}, source: {}, encryption_configured: false },
    })
  )
  await page.route(`${API}/api/credentials/env-status`, (route) =>
    route.fulfill({ json: {} })
  )
  await page.route(`${API}/api/settings*`, (route) => route.fulfill({ json: {} }))

  // Mentor surface: conversation starts empty, memories fill in after consult.
  let consulted = false
  await page.route(`${API}/api/mentor/messages*`, (route) =>
    route.fulfill({ json: [] })
  )
  await page.route(`${API}/api/mentor/memories*`, (route) =>
    route.fulfill({
      json: consulted
        ? [
            {
              id: 'mentor_memory:1',
              question: '提案資料の構成を壁打ちしたい',
              gist: '結論から言うと、課題認識から始めるべきです。',
              sources: ['source:s1'],
              created: '2026-07-23T10:00:00Z',
            },
          ]
        : [],
    })
  )
  await page.route(`${API}/api/mentor/weights`, (route) =>
    route.fulfill({
      json: [
        {
          source_id: 'source:s1',
          title: 'コンサル頭のつくり方',
          weight: 1.0,
          chapter_weights: null,
          auto_factor: 1.12,
          chapters: ['第1章', '第2章'],
        },
      ],
    })
  )
  await page.route(`${API}/api/mentor/consult`, async (route) => {
    consulted = true
    await route.fulfill({
      json: {
        answer:
          '結論から言うと、その並びは自社視点です。『コンサル頭のつくり方』では課題認識から始めることを勧めています。',
        sources: [{ id: 'source:s1', title: 'コンサル頭のつくり方' }],
        message_id: 'mentor_message:m1',
      },
    })
  })
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
  page.on('pageerror', (error) => console.log('[pageerror]', error.message))
  page.on('console', (message) => {
    if (message.type() === 'error') {
      console.log('[console.error]', message.text().slice(0, 300))
    }
  })
  await mockBackends(page)
})

test('consult flow: question -> answer with book chips -> memory panel', async ({
  page,
}) => {
  await page.goto('/mentor')

  // Empty state with sample question chips.
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  const composer = page.getByRole('textbox')
  await expect(composer).toBeVisible()

  // Send a consultation (Cmd+Enter).
  await composer.fill('提案資料の構成を壁打ちしたい')
  await composer.press('ControlOrMeta+Enter')

  // User bubble appears, then the mentor answer with the referenced book chip.
  await expect(page.getByTestId('mentor-message-user')).toContainText(
    '提案資料の構成を壁打ちしたい'
  )
  await expect(page.getByTestId('mentor-message-mentor')).toContainText(
    '結論から言うと'
  )
  // Exact match hits only the chip; the answer text embeds the title in
  // a longer 『…』 sentence.
  await expect(
    page
      .getByTestId('mentor-message-mentor')
      .getByText('コンサル頭のつくり方', { exact: true })
  ).toBeVisible()

  // Memory panel now lists the stored consultation.
  await page.getByRole('button', { name: /記憶|Memories/ }).click()
  await expect(page.getByText('課題認識から始めるべき')).toBeVisible()
})

test('weights tab lists books with sliders and auto-factor badge', async ({
  page,
}) => {
  await page.goto('/mentor')

  await page.getByRole('tab', { name: /学習の傾斜|Learning weights/ }).click()
  const row = page.getByTestId('mentor-weight-row')
  await expect(row).toContainText('コンサル頭のつくり方')
  await expect(row).toContainText('×1.12')

  // Expand chapters.
  await row.getByRole('button').first().click()
  await expect(row).toContainText('第2章')
})
