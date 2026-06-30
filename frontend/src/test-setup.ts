/**
 * test-setup.ts — Vitest global setup (run before each test file).
 *
 * - Pins a fixed, non-UTC timezone so the UTC↔local logic in format.ts is
 *   genuinely exercised (under UTC those conversions are a no-op and would pass
 *   trivially). Node applies a runtime TZ change to subsequent Date operations.
 * - Registers jest-dom matchers and unmounts React trees between tests.
 */

process.env.TZ = 'America/New_York'

import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(() => {
  cleanup()
})
