/**
 * use-url-state.ts — The app's full view state, synced to the URL query string.
 *
 * Schema/table selection plus the per-table view (search, sort, page, page size,
 * and column filters) all live in the URL, so every view is deep-linkable and
 * the browser back/forward buttons work. The backend's SPA catch-all serves
 * index.html for any path, so a shared link like
 *   /?schema=ppm&table=Project&q=apollo&sort=StartDate:desc&f.StatusID=3
 * loads straight into that filtered, sorted view.
 *
 * Navigating to a new table (selectTable) pushes a history entry and clears the
 * per-table view; tweaks within a table replace the entry so typing/paging
 * doesn't flood history.
 */

import { useEffect, useState } from 'react'

import type { FilterOp, SortDirection } from '@/types'

export interface Selection {
  schema: string | null
  table: string | null
}

/**
 * One column filter as held in the URL/UI: an operator plus a raw string value.
 * The value stays a string here (URL-friendly); table-view coerces it to the
 * column's type before querying. For `between` the value is "low..high"; for
 * `isnull`/`notnull` it is empty.
 */
export interface ColumnFilter {
  op: FilterOp
  value: string
}

const FILTER_OPS: readonly FilterOp[] = [
  'eq', 'ne', 'contains', 'startswith', 'endswith',
  'gt', 'gte', 'lt', 'lte', 'between', 'in', 'isnull', 'notnull',
]

/** Operators that carry no value, so they encode as just the operator name. */
const VALUELESS_OPS: ReadonlySet<FilterOp> = new Set<FilterOp>(['isnull', 'notnull'])

/** Decode a URL filter value ("gt:100", "between:1..9", "isnull", or a bare "5"). */
function parseFilter(raw: string): ColumnFilter {
  const sep = raw.indexOf(':')
  if (sep > 0) {
    const op = raw.slice(0, sep) as FilterOp
    if (FILTER_OPS.includes(op)) return { op, value: raw.slice(sep + 1) }
  }
  if (FILTER_OPS.includes(raw as FilterOp)) return { op: raw as FilterOp, value: '' }
  // Hand-typed bare value → equality.
  return { op: 'eq', value: raw }
}

/** Encode a filter for the URL. Returns null when it shouldn't be persisted yet. */
function encodeFilter(f: ColumnFilter): string | null {
  if (VALUELESS_OPS.has(f.op)) return f.op
  if (f.value === '') return null // value-requiring op with nothing typed yet
  return `${f.op}:${f.value}`
}

export interface ViewSort {
  column: string
  direction: SortDirection
}

export interface ViewState {
  schema: string | null
  table: string | null
  search: string
  sort: ViewSort
  page: number
  pageSize: number
  /** column name → operator + raw value; coerced to the column's type before query. */
  filters: Record<string, ColumnFilter>
}

export interface UrlController extends ViewState {
  selectTable: (schema: string, table: string) => void
  setSearch: (search: string) => void
  setSort: (sort: ViewSort) => void
  setPage: (page: number) => void
  setPageSize: (pageSize: number) => void
  setFilters: (filters: Record<string, ColumnFilter>) => void
}

export const DEFAULT_PAGE_SIZE = 50
export const PAGE_SIZE_OPTIONS = [25, 50, 100, 250]

function readState(): ViewState {
  const p = new URLSearchParams(window.location.search)
  const filters: Record<string, ColumnFilter> = {}
  for (const [key, value] of p.entries()) {
    if (key.startsWith('f.') && key.length > 2) filters[key.slice(2)] = parseFilter(value)
  }
  const [column = '', direction = 'asc'] = (p.get('sort') ?? '').split(':')
  return {
    schema: p.get('schema'),
    table: p.get('table'),
    search: p.get('q') ?? '',
    sort: { column, direction: direction === 'desc' ? 'desc' : 'asc' },
    page: Math.max(1, Number(p.get('page')) || 1),
    pageSize: Number(p.get('size')) || DEFAULT_PAGE_SIZE,
    filters,
  }
}

function toQueryString(state: ViewState): string {
  const p = new URLSearchParams()
  if (state.schema) p.set('schema', state.schema)
  if (state.table) p.set('table', state.table)
  if (state.search) p.set('q', state.search)
  if (state.sort.column) p.set('sort', `${state.sort.column}:${state.sort.direction}`)
  if (state.page > 1) p.set('page', String(state.page))
  if (state.pageSize !== DEFAULT_PAGE_SIZE) p.set('size', String(state.pageSize))
  for (const [column, filter] of Object.entries(state.filters)) {
    const encoded = encodeFilter(filter)
    if (encoded !== null) p.set(`f.${column}`, encoded)
  }
  return p.toString()
}

export function useUrlState(): UrlController {
  const [state, setState] = useState<ViewState>(readState)

  // Reflect browser back/forward navigation.
  useEffect(() => {
    const onPopState = () => setState(readState())
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  function commit(next: ViewState, push = false) {
    const qs = toQueryString(next)
    const url = qs ? `?${qs}` : window.location.pathname
    if (push) window.history.pushState(null, '', url)
    else window.history.replaceState(null, '', url)
    setState(next)
  }

  return {
    ...state,
    selectTable: (schema, table) =>
      commit(
        {
          schema,
          table,
          search: '',
          sort: { column: '', direction: 'asc' },
          page: 1,
          pageSize: state.pageSize, // keep page-size preference across tables
          filters: {},
        },
        true,
      ),
    setSearch: (search) => commit({ ...state, search, page: 1 }),
    setSort: (sort) => commit({ ...state, sort, page: 1 }),
    setPage: (page) => commit({ ...state, page }),
    setPageSize: (pageSize) => commit({ ...state, pageSize, page: 1 }),
    setFilters: (filters) => commit({ ...state, filters, page: 1 }),
  }
}
