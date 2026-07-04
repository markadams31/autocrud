import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/lib/api'

// A minimal Response stand-in — only the fields request() reads.
function mkRes(opts: {
  status?: number
  ok?: boolean
  type?: ResponseType
  body?: unknown
}): Response {
  return {
    status: opts.status ?? 200,
    ok: opts.ok ?? true,
    type: opts.type ?? 'basic',
    json: async () => opts.body ?? {},
  } as unknown as Response
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

// The App Service uses unauthenticated_action = RedirectToLoginPage, so an
// expired session answers a protected XHR with a 302 to Entra. With
// redirect: 'manual' that surfaces as an opaque redirect — which must be treated
// as session expiry (re-auth), NOT a database outage.
describe('request() — EasyAuth session expiry via opaque redirect', () => {
  it('refreshes and replays transparently when the session can be recovered', async () => {
    const fetchMock = vi
      .fn()
      // 1) the API call → EasyAuth redirect, surfaced as an opaque redirect
      .mockResolvedValueOnce(mkRes({ status: 0, ok: false, type: 'opaqueredirect' }))
      // 2) /.auth/refresh succeeds
      .mockResolvedValueOnce(mkRes({ status: 200, ok: true }))
      // 3) the replayed API call succeeds
      .mockResolvedValueOnce(mkRes({ status: 200, ok: true, body: { user_id: 'alice' } }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(api.me()).resolves.toEqual({ user_id: 'alice' })
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(fetchMock.mock.calls[1][0]).toBe('/.auth/refresh')
  })

  it('throws UNAUTHENTICATED — not DATABASE_UNAVAILABLE — when the session is unrecoverable', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(mkRes({ status: 0, ok: false, type: 'opaqueredirect' })) // API → redirect
      .mockResolvedValueOnce(mkRes({ status: 401, ok: false })) // /.auth/refresh fails
    vi.stubGlobal('fetch', fetchMock)

    await expect(api.me()).rejects.toMatchObject({ code: 'UNAUTHENTICATED', status: 401 })
  })

  it('still reports a genuine network failure as DATABASE_UNAVAILABLE', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(api.me()).rejects.toMatchObject({ code: 'DATABASE_UNAVAILABLE', status: 0 })
  })
})
