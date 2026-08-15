import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests/ha',
  fullyParallel: false,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:18080',
    ...devices['Desktop Chrome'],
    channel: 'msedge',
  },
})
