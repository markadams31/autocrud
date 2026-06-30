import { defineConfig, mergeConfig } from 'vitest/config'

import viteConfig from './vite.config'

// Test config kept separate from the build config (vite.config.ts). It reuses
// the app's plugins (so .tsx transforms exactly as in the build) and the `@`
// alias via mergeConfig, then runs under jsdom so component/hook tests can
// render. A setup file pins a non-UTC timezone — without that, format.ts's
// UTC↔local conversions would pass trivially.
export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test-setup.ts'],
      // src unit/component tests + the e2e mock's contract test (the Playwright
      // specs themselves are run by Playwright, not Vitest).
      include: ['src/**/*.test.{ts,tsx}', 'e2e/**/*.contract.test.{ts,tsx}'],
      coverage: {
        provider: 'v8',
        include: ['src/**/*.{ts,tsx}'],
        // shadcn UI primitives are vendored source we own but don't author;
        // entrypoints/wiring carry no testable logic.
        exclude: ['src/components/ui/**', 'src/main.tsx', 'src/App.tsx', 'src/**/*.test.*'],
      },
    },
  }),
)
