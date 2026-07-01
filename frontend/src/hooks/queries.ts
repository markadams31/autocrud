/**
 * queries.ts — TanStack Query hooks wrapping the API client.
 *
 * All server state flows through here: metadata, row pages, FK options, and
 * the create/update/delete mutations. Centralising the query keys keeps cache
 * invalidation correct — a mutation on a table invalidates exactly the row
 * pages for that table and nothing else.
 */

import {
  keepPreviousData,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
  type QueryClient,
  type QueryKey,
} from '@tanstack/react-query'

import { api, encodePk } from '@/lib/api'
import { sizeBucket, trackEvent } from '@/lib/telemetry'
import type {
  BulkCreateRequest,
  BulkDeleteRequest,
  BulkUpdateRequest,
  ColumnMeta,
  QueryRequest,
  QueryResponse,
  Row,
  TableMeta,
} from '@/types'

// Metadata changes rarely (only on a schema refresh), so it can sit fresh for
// a while. Row data is left at the default staleness — refetched readily.
const META_STALE_TIME = 5 * 60 * 1000

// Background-poll cadence for the active table's rows. The server is always the
// source of truth (no server-side cache), so a write in another client/tab is
// invisible here until something triggers a refetch. Window-focus refetch is
// disabled globally (it reshuffles the grid on every tab switch), so without
// this an idle viewer could stay stale indefinitely. A steady, predictable
// interval bounds that staleness; keepPreviousData makes each poll update in
// place with no flash, and the grid's top fetch-bar already signals it.
const ROWS_REFETCH_INTERVAL = 30_000

export const queryKeys = {
  me: ['me'] as const,
  version: ['version'] as const,
  schemas: ['schemas'] as const,
  tables: (schema: string) => ['tables', schema] as const,
  tableMeta: (schema: string, table: string) => ['tableMeta', schema, table] as const,
  options: (schema: string, table: string, column: string) =>
    ['options', schema, table, column] as const,
  /** Prefix matching every cached FK option list — used to invalidate writes. */
  optionsAll: ['options'] as const,
  record: (schema: string, table: string, pk: string) =>
    ['record', schema, table, pk] as const,
  rows: (schema: string, table: string, req: QueryRequest) =>
    ['rows', schema, table, req] as const,
  /** Prefix matching every cached page for a table — used to invalidate writes. */
  rowsAll: (schema: string, table: string) => ['rows', schema, table] as const,
}

// ── Identity ─────────────────────────────────────────────────────────────────

/**
 * The signed-in user (GET /me). Fixed for the session, so it never goes stale
 * and isn't retried — if the identity headers aren't present (e.g. local dev
 * without the auth proxy) the UI simply shows no user rather than retrying.
 */
export function useMe() {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: () => api.me(),
    staleTime: Infinity,
    retry: false,
  })
}

/**
 * The running backend build — commit SHA + build time (GET /version). Static for
 * the life of the process, so it never goes stale; a redeploy reloads the SPA.
 * Shown in the About dialog.
 */
export function useVersion() {
  return useQuery({
    queryKey: queryKeys.version,
    queryFn: () => api.version(),
    staleTime: Infinity,
  })
}

// ── Metadata ─────────────────────────────────────────────────────────────────

/** Returns the connected database name and its schema list (GET /meta). */
export function useSchemas() {
  return useQuery({
    queryKey: queryKeys.schemas,
    queryFn: () => api.listSchemas(),
    staleTime: META_STALE_TIME,
  })
}

export function useTables(schema: string | null) {
  return useQuery({
    queryKey: queryKeys.tables(schema ?? ''),
    queryFn: () => api.listTables(schema!).then((r) => r.tables),
    enabled: schema != null,
    staleTime: META_STALE_TIME,
  })
}

export function useTableMeta(schema: string | null, table: string | null) {
  return useQuery({
    queryKey: queryKeys.tableMeta(schema ?? '', table ?? ''),
    queryFn: () => api.describeTable(schema!, table!),
    enabled: schema != null && table != null,
    staleTime: META_STALE_TIME,
  })
}

// ── Rows ─────────────────────────────────────────────────────────────────────

export function useRows(
  schema: string | null,
  table: string | null,
  req: QueryRequest,
) {
  return useQuery({
    queryKey: queryKeys.rows(schema ?? '', table ?? '', req),
    queryFn: () => api.query(schema!, table!, req),
    enabled: schema != null && table != null,
    // Keep the previous page on screen while the next loads — no flash to empty.
    placeholderData: keepPreviousData,
    // Poll so edits from other clients/tabs surface without a manual refresh.
    // Only the active table's currently-viewed query polls (prefetched adjacent
    // pages don't); pauses automatically while the tab is hidden.
    refetchInterval: ROWS_REFETCH_INTERVAL,
  })
}

/**
 * A single record by primary key — used by the FK hover-card preview. Gated by
 * `enabled` so it only fetches when a card actually opens, and cached so
 * hovering the same reference again is instant.
 */
export function useRecord(
  schema: string,
  table: string,
  pk: string,
  enabled: boolean,
) {
  return useQuery({
    queryKey: queryKeys.record(schema, table, pk),
    queryFn: () => api.getRow(schema, table, pk),
    enabled: enabled && Boolean(schema && table && pk),
    staleTime: META_STALE_TIME,
  })
}

/** Single FK column's options, for a form dropdown. */
export function useOptions(
  schema: string | null,
  table: string | null,
  column: string | null,
) {
  return useQuery({
    queryKey: queryKeys.options(schema ?? '', table ?? '', column ?? ''),
    queryFn: () => api.options(schema!, table!, column!),
    enabled: schema != null && table != null && column != null,
    staleTime: META_STALE_TIME,
  })
}

/**
 * Resolve every FK column on a table to a value→label map in parallel, so the
 * grid can show "Engineering" instead of a raw department id. This is the
 * display_column heuristic made visible: each FK's label comes from its
 * target table's chosen display column.
 */
export type FkLabelMap = Record<string, Map<string, string>>

export function useForeignKeyLabels(meta: TableMeta | undefined) {
  const fkColumns: ColumnMeta[] = meta?.columns.filter((c) => c.foreign_key) ?? []

  return useQueries({
    queries: fkColumns.map((col) => ({
      queryKey: queryKeys.options(meta!.schema, meta!.name, col.name),
      queryFn: () => api.options(meta!.schema, meta!.name, col.name),
      staleTime: META_STALE_TIME,
    })),
    combine: (results): { labels: FkLabelMap; loading: boolean } => {
      const labels: FkLabelMap = {}
      fkColumns.forEach((col, i) => {
        const data = results[i]?.data
        if (data) {
          labels[col.name] = new Map(data.map((o) => [String(o.value), o.label]))
        }
      })
      return { labels, loading: results.some((r) => r.isLoading) }
    },
  })
}

// ── Mutations ────────────────────────────────────────────────────────────────
//
// Update is optimistic: the cached row pages are patched immediately so the UI
// reacts at once, then reconciled against the server on settle (which also picks
// up server-computed columns like ModifiedDate). On error the snapshot is rolled
// back. Create is not optimistic — the server assigns the PK and computed values
// and decides where the row sorts, so we simply invalidate and let the fresh
// page render it. Single-row delete lives in use-undoable-delete (optimistic
// removal + an Undo grace period); the bulk mutations are below.

type RowsSnapshot = [QueryKey, QueryResponse | undefined][]

function rollback(qc: QueryClient, ctx?: { previous: RowsSnapshot }) {
  ctx?.previous.forEach(([key, data]) => qc.setQueryData(key, data))
}

/**
 * After any write, refresh the table's row pages AND every FK option list — a
 * create/delete changes which rows other tables can reference, and an update can
 * change the label shown for one. (Option lists are small and cached, so the
 * broad invalidation is cheap and keeps FK dropdowns from offering stale or
 * just-deleted rows.)
 */
export function invalidateTableWrites(qc: QueryClient, schema: string, table: string) {
  void qc.invalidateQueries({ queryKey: queryKeys.rowsAll(schema, table) })
  void qc.invalidateQueries({ queryKey: queryKeys.optionsAll })
}

/**
 * Create is not optimistic: the server assigns the PK, computed columns, and the
 * row's sort position, so inserting a placeholder would only appear at the wrong
 * spot and then jump. Instead we invalidate and let the fresh page render it —
 * the caller flashes the new row so it's still obvious where it landed.
 */
export function useCreateRow(schema: string, table: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (values: Row) => api.createRow(schema, table, values),
    onSuccess: () => invalidateTableWrites(qc, schema, table),
  })
}

export function useUpdateRow(
  schema: string,
  table: string,
  primaryKey: string[],
  /** rowversion column name (TableMeta.concurrency_token), if the table has one. */
  tokenColumn?: string | null,
) {
  const qc = useQueryClient()
  const rowsKey = queryKeys.rowsAll(schema, table)
  return useMutation({
    mutationFn: ({ row, values }: { row: Row; values: Row }) => {
      // Send the row's rowversion as If-Match so the server rejects the write if
      // the row changed since we read it (optimistic concurrency). Omitted when
      // the table has no rowversion → last-writer-wins.
      const ifMatch =
        tokenColumn && row[tokenColumn] != null ? String(row[tokenColumn]) : undefined
      return api.updateRow(schema, table, encodePk(row, primaryKey), values, ifMatch)
    },
    onMutate: async ({ row, values }) => {
      await qc.cancelQueries({ queryKey: rowsKey })
      const previous = qc.getQueriesData<QueryResponse>({ queryKey: rowsKey })
      // Identify the row by its encoded primary key — the same identity scheme
      // used everywhere else (the URL path, the undoable-delete matcher).
      const key = encodePk(row, primaryKey)
      qc.setQueriesData<QueryResponse>({ queryKey: rowsKey }, (old) =>
        old
          ? {
              ...old,
              data: old.data.map((r) => (encodePk(r, primaryKey) === key ? { ...r, ...values } : r)),
            }
          : old,
      )
      return { previous }
    },
    // Replace the cached row with the server's response so the fresh rowversion
    // (and any server-computed columns) is in place at once. Without this a rapid
    // second edit of the same row would send the now-stale token and hit a
    // spurious CONFLICT before the settle refetch lands.
    onSuccess: (updated, { row }) => {
      const key = encodePk(row, primaryKey)
      qc.setQueriesData<QueryResponse>({ queryKey: rowsKey }, (old) =>
        old
          ? { ...old, data: old.data.map((r) => (encodePk(r, primaryKey) === key ? updated : r)) }
          : old,
      )
    },
    onError: (_err, _vars, ctx) => rollback(qc, ctx),
    onSettled: () => invalidateTableWrites(qc, schema, table),
  })
}

/**
 * Bulk delete (atomic on the server). Not optimistic: in "all matching" mode
 * the client doesn't hold every affected row, so there's nothing reliable to
 * patch — we show a pending state and invalidate on success so the grid reflects
 * the server's post-delete truth.
 */
export function useBulkDelete(schema: string, table: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: BulkDeleteRequest) => api.bulkDelete(schema, table, body),
    onSuccess: (res, body) => {
      trackEvent('bulk_write', {
        op: 'delete', schema, table,
        target: body.all_matching ? 'all_matching' : 'ids',
        count: sizeBucket(res.deleted), outcome: 'ok',
      })
      invalidateTableWrites(qc, schema, table)
    },
    onError: (err, body) =>
      trackEvent('bulk_write', {
        op: 'delete', schema, table,
        target: body.all_matching ? 'all_matching' : 'ids',
        outcome: 'error', code: (err as { code?: string }).code ?? 'error',
      }),
  })
}

/**
 * Bulk update (atomic on the server). Not optimistic, for the same reason as
 * bulk delete: "all matching" affects rows the client doesn't hold, and even an
 * explicit-id update pulls in server-computed columns (a recomputed value, a
 * refreshed ModifiedDate). Invalidate on success and let the refetch show truth.
 */
export function useBulkUpdate(schema: string, table: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: BulkUpdateRequest) => api.bulkUpdate(schema, table, body),
    onSuccess: (res, body) => {
      trackEvent('bulk_write', {
        op: 'update', schema, table,
        target: body.all_matching ? 'all_matching' : 'ids',
        count: sizeBucket(res.updated), outcome: 'ok',
      })
      invalidateTableWrites(qc, schema, table)
    },
    onError: (err, body) =>
      trackEvent('bulk_write', {
        op: 'update', schema, table,
        target: body.all_matching ? 'all_matching' : 'ids',
        outcome: 'error', code: (err as { code?: string }).code ?? 'error',
      }),
  })
}

/**
 * Bulk create — import many rows atomically. Like the other bulk mutations it's
 * not optimistic: the server assigns identity keys, computed values, and server
 * defaults and decides where each row sorts, so we invalidate on success and let
 * the refetch render the imported rows.
 */
export function useBulkCreate(schema: string, table: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: BulkCreateRequest) => api.bulkCreate(schema, table, body),
    onSuccess: () => invalidateTableWrites(qc, schema, table),
  })
}

/**
 * Re-reflect the database schema (POST /admin/refresh), then invalidate every
 * cached query so the whole UI rebuilds against the new snapshot. The broad
 * invalidation is deliberate: a DDL change can alter any table, column, or
 * constraint, so nothing cached is guaranteed current afterwards.
 */
export function useRefreshSchema() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.refreshSchema(),
    onSuccess: () => {
      void qc.invalidateQueries()
    },
  })
}
