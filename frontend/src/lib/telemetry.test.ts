import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Shared SDK mocks. vi.hoisted so they exist when the (hoisted) vi.mock factories
// below reference them.
// Regular `function` (not arrow) so the mocks are constructable with `new`.
const { AICtor, loadAppInsights, trackEventMock, trackPageView } = vi.hoisted(() => {
  const loadAppInsights = vi.fn()
  const trackEventMock = vi.fn()
  const trackPageView = vi.fn()
  const AICtor = vi.fn(function () {
    return {
      loadAppInsights,
      trackEvent: trackEventMock,
      trackException: vi.fn(),
      trackPageView,
    }
  })
  return { AICtor, loadAppInsights, trackEventMock, trackPageView }
})

vi.mock('@microsoft/applicationinsights-web', () => ({
  ApplicationInsights: AICtor,
  DistributedTracingModes: { AI_AND_W3C: 2 },
}))
vi.mock('@microsoft/applicationinsights-react-js', () => ({
  ReactPlugin: class {
    identifier = 'ApplicationInsightsAnalytics'
  },
}))

// Fresh module state per test (the module holds `appInsights` at module scope).
async function loadModule() {
  vi.resetModules()
  return import('@/lib/telemetry')
}

function stubConfig(connectionString: string | null) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ applicationInsights: { connectionString } }),
    }),
  )
}

beforeEach(() => vi.clearAllMocks())
afterEach(() => vi.unstubAllGlobals())

describe('initTelemetry', () => {
  it('loads the SDK when a connection string is configured', async () => {
    stubConfig('InstrumentationKey=abc;IngestionEndpoint=https://x.in.applicationinsights.azure.com/')
    const t = await loadModule()
    await t.initTelemetry()
    expect(AICtor).toHaveBeenCalledOnce()
    expect(loadAppInsights).toHaveBeenCalledOnce()
    expect(trackPageView).toHaveBeenCalledOnce()
  })

  it('is a clean no-op when no connection string is configured (local/dev)', async () => {
    stubConfig(null)
    const t = await loadModule()
    await t.initTelemetry()
    expect(AICtor).not.toHaveBeenCalled()
  })

  it('stays a no-op if /config fetch fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network')))
    const t = await loadModule()
    await t.initTelemetry()
    expect(AICtor).not.toHaveBeenCalled()
  })
})

describe('trackEvent', () => {
  it('does not touch the SDK before init', async () => {
    const t = await loadModule()
    t.trackEvent('row_delete_undo', { raced: true })
    expect(trackEventMock).not.toHaveBeenCalled()
  })

  it('forwards to the SDK after init', async () => {
    stubConfig('InstrumentationKey=abc')
    const t = await loadModule()
    await t.initTelemetry()
    t.trackEvent('row_delete_undo', { raced: true })
    expect(trackEventMock).toHaveBeenCalledWith({ name: 'row_delete_undo' }, { raced: true })
  })
})

describe('sizeBucket', () => {
  it('maps a count to a low-cardinality bucket (boundaries inclusive)', async () => {
    const { sizeBucket } = await loadModule()
    expect(sizeBucket(-5)).toBe('0')
    expect(sizeBucket(0)).toBe('0')
    expect(sizeBucket(1)).toBe('1')
    expect(sizeBucket(2)).toBe('2-10')
    expect(sizeBucket(10)).toBe('2-10')
    expect(sizeBucket(11)).toBe('11-100')
    expect(sizeBucket(100)).toBe('11-100')
    expect(sizeBucket(101)).toBe('101-1000')
    expect(sizeBucket(1000)).toBe('101-1000')
    expect(sizeBucket(1001)).toBe('1000+')
  })
})
