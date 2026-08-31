import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  // The plan page mounts a model-viewer and a large map canvas.  Running two
  // browser workers concurrently can starve the browser's rendering queue and
  // make an otherwise healthy page miss Playwright's navigation timeout.  A
  // single worker keeps the acceptance suite deterministic; it still covers
  // every configured viewport and browser.
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:5173',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'desktop-1366',
      testIgnore: '**/firefox-compat.spec.ts',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1366, height: 768 } },
    },
    {
      name: 'desktop-1920',
      testIgnore: '**/firefox-compat.spec.ts',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1920, height: 1080 } },
    },
    {
      name: 'firefox-3d',
      testMatch: '**/firefox-compat.spec.ts',
      use: { ...devices['Desktop Firefox'], viewport: { width: 1366, height: 768 } },
    },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: true,
  },
})
