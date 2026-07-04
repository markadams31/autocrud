import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright config for the frontend SPA.
 *
 * The app normally talks to the FastAPI backend (which needs Azure SQL +
 * EasyAuth), so the e2e tests stub every /meta and /api response with
 * page.route — no backend required. We serve the production build via
 * `vite preview` so the tests exercise exactly what ships.
 */
const PORT = 4173

export default defineConfig({
  testDir: './e2e',
  // *.contract.test.ts files are Vitest tests that live next to the mock they
  // guard — Playwright must not try to run them.
  testIgnore: '**/*.contract.test.ts',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list']],
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: `npx vite preview --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
