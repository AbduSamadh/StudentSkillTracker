import { defineConfig, devices } from '@playwright/test'

// Runs against a running stack with the demo school seeded (`python -m app.cli seed-demo`).
// E2E_BASE_URL defaults to `vite preview`, which proxies /api to the API like nginx does.
export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false, // the tests share one seeded database and write to it
  workers: 1,
  retries: 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:4173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: process.env.PW_CHROMIUM_PATH ? { executablePath: process.env.PW_CHROMIUM_PATH } : {},
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1360, height: 900 } } }],
})
