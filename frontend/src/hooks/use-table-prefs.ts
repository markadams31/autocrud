/**
 * use-table-prefs.ts — Local, persisted grid preferences.
 *
 * Density is a single global preference; hidden columns are remembered per
 * table. Both live in localStorage so a user's layout survives reloads. These
 * are presentation-only and intentionally NOT in the URL (the URL carries the
 * shareable *data* view — search/sort/filters — not personal layout).
 */

import { useState } from 'react'

export type Density = 'comfortable' | 'compact'

const DENSITY_KEY = 'autocrud-density'
const HIDDEN_PREFIX = 'autocrud-hidden:'

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
