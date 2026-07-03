/**
 * use-table-prefs.ts — Local, persisted grid preferences.
 *
 * Density is a single global preference; hidden columns, column order, column
 * widths, and saved views are per-table. All live in localStorage so a user's
 * layout survives reloads. These are presentation-only and intentionally NOT in
 * the URL (the URL carries the shareable *data* view — search/sort/filters —
 * not personal layout).
 */

import { useState } from 'react'

import type { SortSpec } from '@/types'
import type { ColumnFilter } from '@/hooks/use-url-state'

export type Density = 'comfortable' | 'compact'

const DENSITY_KEY = 'autocrud-density'
const HIDDEN_PREFIX = 'autocrud-hidden:'
const ORDER_PREFIX = 'autocrud-order:'
const WIDTHS_PREFIX = 'autocrud-widths:'
const VIEWS_PREFIX = 'autocrud-views:'

// ── Density (global) ─────────────────────────────────────────────────────────

function readDensity(): Density {
  return localStorage.getItem(DENSITY_KEY) === 'compact' ? 'compact' : 'comfortable'
}

export function useDensity() {
  const [density, setDensityState] = useState<Density>(readDensity)
  function setDensity(next: Density) {
    localStorage.setItem(DENSITY_KEY, next)
    setDensityState(next)
  }
  return { density, setDensity }
}

// ── Hidden columns (per table) ───────────────────────────────────────────────

function readHidden(key: string): Set<string> {
  try {
    const raw = localStorage.getItem(HIDDEN_PREFIX + key)
    const parsed = raw ? JSON.parse(raw) : []
    return new Set(Array.isArray(parsed) ? parsed : [])
  } catch {
    return new Set()
  }
}

/**
 * Hidden-column set for one table. The owning view is keyed by schema.table and
 * remounts when the table changes, so the initial read is always for the right
 * table.
 */
export function useHiddenColumns(schema: string, table: string) {
  const storageKey = `${schema}.${table}`
  const [hidden, setHidden] = useState<Set<string>>(() => readHidden(storageKey))

  function persist(next: Set<string>) {
    localStorage.setItem(HIDDEN_PREFIX + storageKey, JSON.stringify([...next]))
    setHidden(next)
  }

  function toggle(name: string) {
    const next = new Set(hidden)
    if (next.has(name)) next.delete(name)
    else next.add(name)
    persist(next)
  }

  function reset() {
    persist(new Set())
  }

  /** Replace the whole hidden set at once (e.g. a bulk "hide these" action). */
  function replace(next: Iterable<string>) {
    persist(new Set(next))
  }

  return { hidden, toggle, reset, replace }
}

// ── Column order (per table) ─────────────────────────────────────────────────

function readOrder(key: string): string[] {
  try {
    const raw = localStorage.getItem(ORDER_PREFIX + key)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

/**
 * Persisted column display order for one table. An empty array means "use the
 * schema-reflected order". Columns not present in the order array (e.g. newly
 * added columns) are appended after the saved columns.
 */
export function useColumnOrder(schema: string, table: string) {
  const storageKey = `${schema}.${table}`
  const [columnOrder, setOrderState] = useState<string[]>(() => readOrder(storageKey))

  function setColumnOrder(next: string[]) {
    localStorage.setItem(ORDER_PREFIX + storageKey, JSON.stringify(next))
    setOrderState(next)
  }

  function resetColumnOrder() {
    localStorage.removeItem(ORDER_PREFIX + storageKey)
    setOrderState([])
  }

  return { columnOrder, setColumnOrder, resetColumnOrder }
}

// ── Column widths (per table) ────────────────────────────────────────────────

function readWidths(key: string): Record<string, number> {
  try {
    const raw = localStorage.getItem(WIDTHS_PREFIX + key)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}

/** Per-column width overrides. Absent entry → fall back to the type-derived default. */
export function useColumnWidths(schema: string, table: string) {
  const storageKey = `${schema}.${table}`
  const [columnWidths, setWidthsState] = useState<Record<string, number>>(
    () => readWidths(storageKey),
  )

  function setColumnWidth(column: string, width: number) {
    const next = { ...columnWidths, [column]: width }
    localStorage.setItem(WIDTHS_PREFIX + storageKey, JSON.stringify(next))
    setWidthsState(next)
  }

  function resetColumnWidths() {
    localStorage.removeItem(WIDTHS_PREFIX + storageKey)
    setWidthsState({})
  }

  return { columnWidths, setColumnWidth, resetColumnWidths }
}

// ── Saved views (per table) ──────────────────────────────────────────────────

/** A named snapshot of the active data view + layout. */
export interface SavedView {
  id: string
  name: string
  filters: Record<string, ColumnFilter>
  sort: SortSpec[]
  hiddenColumns: string[]
  columnOrder: string[]
}

function readViews(key: string): SavedView[] {
  try {
    const raw = localStorage.getItem(VIEWS_PREFIX + key)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export function useSavedViews(schema: string, table: string) {
  const storageKey = `${schema}.${table}`
  const [views, setViewsState] = useState<SavedView[]>(() => readViews(storageKey))

  function persist(next: SavedView[]) {
    localStorage.setItem(VIEWS_PREFIX + storageKey, JSON.stringify(next))
    setViewsState(next)
  }

  function saveView(view: Omit<SavedView, 'id'>): SavedView {
    const newView: SavedView = { ...view, id: crypto.randomUUID() }
    persist([...views, newView])
    return newView
  }

  function deleteView(id: string) {
    persist(views.filter((v) => v.id !== id))
  }

  function renameView(id: string, name: string) {
    persist(views.map((v) => (v.id === id ? { ...v, name } : v)))
  }

  return { views, saveView, deleteView, renameView }
}
