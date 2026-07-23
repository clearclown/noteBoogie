import { defineConfig, devices } from '@playwright/test'

/**
 * E2E tests for the frontend. Backend/gateway responses are mocked with
 * page.route() so these run hermetically against `next dev` alone.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: 'http://localhost:3100',
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    // CI uses a production build: next dev compiles pages on demand and the
    // first navigation can blow the test timeout on slow runners.
    command: process.env.CI
      ? 'npm run build && npx next start -p 3100'
      : 'npx next dev -p 3100',
    url: 'http://localhost:3100',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
