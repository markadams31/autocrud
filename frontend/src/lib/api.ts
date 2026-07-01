/**
 * api.ts — The single fetch wrapper for the whole app.
 *
 * Responsibilities:
 *   - Build same-origin URLs. In dev, Vite proxies /api, /meta, /admin to the
 *     auth proxy (which injects EasyAuth headers); in prod the SPA and API
 *     share an origin. Either way the paths are root-relative — no base URL.
 *   - Parse the standard error contract (errors.py) into a typed ApiError so
 *     callers can switch on `code` and surface `fields` on forms.
 *   - Encode primary keys for row-addressing routes.
 */

import type {
  ApiErrorBody,
  BuildInfo,
  BulkCreateRequest,
  BulkCreateResponse,
  BulkDeleteRequest,
  BulkDeleteResponse,
  BulkUpdateRequest,
  BulkUpdateResponse,
  ErrorCode,
  FieldOption,
  Me,
  QueryRequest,
  QueryResponse,
  Row,
  SchemaList,
  SchemaTables,
  TableMeta,
} from '@/types'

/**
 * Typed error thrown for any non-2xx response. Carries the machine-readable
 * `code` the UI branches on, a safe human `message`, and optional per-field
 * detail for form highlighting.
 */
export class ApiError extends Error {
  readonly code: ErrorCode
  readonly status: number
  readonly fields?: Record<string, string>
  /** Bulk import: index of the row a database constraint rejected. */
  readonly row?: number
  /** Bulk import: per-row field errors from up-front validation. */
  readonly rows?: Record<string, Record<string, string>>

  constructor(status: number, body: Partial<ApiErrorBody>) {
    super(body.message ?? 'Something went wrong.')
    this.name = 'ApiError'
    this.status = status
    this.code = body.code ?? 'INTERNAL_ERROR'
    this.fields = body.fields
    this.row = body.row
    this.rows = body.rows
  }
}

// ── EasyAuth session refresh ─────────────────────────────────────────────────
//
// App Service EasyAuth doesn't refresh the Azure SQL token automatically. When
// the session lapses, a protected XHR comes back as a 401 or — because the App
// Service uses unauthenticated_action = RedirectToLoginPage — an opaque redirect
// to the Entra sign-in page. These built-in EasyAuth endpoints refresh the token
// store (using the stored refresh token) and, as a last resort, send the user
// back through sign-in. Both are same-origin in production; locally the dev auth
// proxy keeps tokens fresh so this path is rarely exercised.
const AUTH_REFRESH_PATH = '/.auth/refresh'
const AUTH_LOGIN_PATH = '/.auth/login/aad'

// One in-flight refresh shared across all callers, so a burst of 401s (every
// query failing at once when the token lapses) triggers a single refresh.
let refreshInFlight: Promise<boolean> | null = null

function refreshSession(): Promise<boolean> {
  refreshInFlight ??= fetch(AUTH_REFRESH_PATH, { credentials: 'same-origin', redirect: 'manual' })
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => {
      refreshInFlight = null
    })
  return refreshInFlight
}

function redirectToLogin(): void {
  const returnTo = window.location.pathname + window.location.search
  window.location.assign(
    `${AUTH_LOGIN_PATH}?post_login_redirect_uri=${encodeURIComponent(returnTo)}`,
  )
}

async function request<T>(path: string, init?: RequestInit, retried = false): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      ...init,
      // `manual` so an expired-session 302 to the Entra login page comes back as
      // an opaque redirect we can detect (below) instead of being followed
      // cross-origin — which fails CORS and is indistinguishable from the server
      // being down. The API itself never legitimately redirects.
      redirect: 'manual',
      headers: {
        'Content-Type': 'application/json',
        ...init?.headers,
      },
    })
  } catch {
    // Network-level failure (server down, proxy not running, offline).
    throw new ApiError(0, {
      code: 'DATABASE_UNAVAILABLE',
      message: 'Could not reach the server. Check your connection and try again.',
    })
  }

  // An expired EasyAuth session shows up as a 401 or — because the App Service
  // redirects rather than 401s (unauthenticated_action = RedirectToLoginPage) —
  // an opaque redirect to the Entra sign-in page. Either way, refresh the session
  // transparently (once) and replay so the user never notices; if the refresh
  // can't recover it, hand them off to sign in again.
  if (res.status === 401 || res.type === 'opaqueredirect') {
    if (!retried && (await refreshSession())) {
      return request<T>(path, init, true)
    }
    if (import.meta.env.PROD) redirectToLogin()
    throw new ApiError(401, {
      code: 'UNAUTHENTICATED',
      message: 'Your session has expired. Please sign in again.',
    })
  }

  if (res.status === 204) return undefined as T

  // Every response — success or error — is JSON from this API.
  const body = await res.json().catch(() => ({}))

  if (!res.ok) {
    throw new ApiError(res.status, body as ApiErrorBody)
  }
  return body as T
}

const enc = encodeURIComponent

/** Encode a row's primary key for the URL path: comma-separated, PK order. */
export function encodePk(row: Row, primaryKey: string[]): string {
  return primaryKey.map((name) => enc(String(row[name]))).join(',')
}

// Base paths for a table's data (/api) and metadata (/meta) endpoints. Built
// once here so every endpoint below shares the same encoding.
const apiPath = (schema: string, table: string) => `/api/${enc(schema)}/${enc(table)}`
const metaPath = (schema: string, table: string) => `/meta/${enc(schema)}/${enc(table)}`

const jsonPost = (body: unknown) => ({ method: 'POST', body: JSON.stringify(body) })

// ── Endpoint helpers ─────────────────────────────────────────────────────────

export const api = {
  me: () => request<Me>('/me'),

  /** The running backend build — commit SHA + build time (GET /version). */
  version: () => request<BuildInfo>('/version'),

  /** Re-reflect the database schema and rebuild the API (POST /admin/refresh). */
  refreshSchema: () =>
    request<{ status: string; total: number; schemas: Record<string, string[]> }>(
      '/admin/refresh',
      { method: 'POST' },
    ),

  listSchemas: () => request<SchemaList>('/meta'),

  listTables: (schema: string) => request<SchemaTables>(`/meta/${enc(schema)}`),

  describeTable: (schema: string, table: string) =>
    request<TableMeta>(metaPath(schema, table)),

  options: (schema: string, table: string, column: string) =>
    request<FieldOption[]>(`${metaPath(schema, table)}/options/${enc(column)}`),

  getRow: (schema: string, table: string, pk: string) =>
    request<Row>(`${apiPath(schema, table)}/${enc(pk)}`),

  query: (schema: string, table: string, body: QueryRequest) =>
    request<QueryResponse>(`${apiPath(schema, table)}/query`, jsonPost(body)),

  createRow: (schema: string, table: string, values: Row) =>
    request<Row>(apiPath(schema, table), jsonPost(values)),

  // pk is already URL-encoded by encodePk before it reaches here. `ifMatch` is
  // the row's rowversion token (from a prior read); when supplied it's sent as
  // If-Match so the server rejects the write if the row changed meanwhile (409).
  updateRow: (schema: string, table: string, pk: string, values: Row, ifMatch?: string) =>
    request<Row>(`${apiPath(schema, table)}/${pk}`, {
      method: 'PATCH',
      body: JSON.stringify(values),
      headers: ifMatch ? { 'If-Match': ifMatch } : undefined,
    }),

  deleteRow: (schema: string, table: string, pk: string, ifMatch?: string) =>
    request<{ deleted: number }>(`${apiPath(schema, table)}/${pk}`, {
      method: 'DELETE',
      headers: ifMatch ? { 'If-Match': ifMatch } : undefined,
    }),

  bulkDelete: (schema: string, table: string, body: BulkDeleteRequest) =>
    request<BulkDeleteResponse>(`${apiPath(schema, table)}/bulk-delete`, jsonPost(body)),

  bulkUpdate: (schema: string, table: string, body: BulkUpdateRequest) =>
    request<BulkUpdateResponse>(`${apiPath(schema, table)}/bulk-update`, jsonPost(body)),

  bulkCreate: (schema: string, table: string, body: BulkCreateRequest) =>
    request<BulkCreateResponse>(`${apiPath(schema, table)}/bulk-create`, jsonPost(body)),
}
