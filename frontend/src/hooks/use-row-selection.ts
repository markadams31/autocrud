/**
 * use-row-selection.ts — Row-selection state for the grid's bulk actions.
 *
 * Tracks two layers of selection: the encoded primary keys of individually
 * ticked rows on the current page (`selectedKeys`), and the extended "every row
 * matching the current query" mode (`allMatching`). Selection is scoped to the
 * current page and query, so it resets whenever either changes — the caller
 * passes a `resetKey` that captures the search/filter/page identity, since the
 * loaded rows change underneath the selection when the query does.
 *
 * Extracted from table-view so the orchestration component stays focused on
 * composition and this logic is unit-testable in isolation.
 */
import { useEffect, useState } from 'react'

import { encodePk } from '@/lib/api'
import type { Row } from '@/types'

export interface RowSelection {
  /** Encoded PKs of the individually-ticked rows on the current page. */
  selectedKeys: Set<string>
  /** True when the selection is "every row matching the query", not just ticks. */
  allMatching: boolean
  /** The ticked rows currently loaded (intersection of selectedKeys and rows). */
  selectedRows: Row[]
  /** Rows a confirmed action affects: the whole matching set, or the ticks. */
  effectiveCount: number
  /** Whether "select all matching" can be offered (page full, more rows exist). */
  canSelectAllMatching: boolean
  /** Encode a row to its selection key — shared with the caller's flash logic. */
  keyOf: (row: Row) => string
  isSelected: (row: Row) => boolean
  toggleRow: (row: Row) => void
  toggleAllLoaded: () => void
  selectAllMatching: () => void
  clear: () => void
}

export function useRowSelection(
  rows: Row[],
  primaryKey: string[],
  total: number,
  resetKey: string,
): RowSelection {
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(() => new Set())
  const [allMatching, setAllMatching] = useState(false)

  const keyOf = (row: Row) => encodePk(row, primaryKey)

  // Reset when the query/page changes: the loaded rows change underneath the
  // selection, so acting on stale ticks would target rows the user can't see.
  // (Cross-page bulk action is the "select all matching" path, which acts
  // set-based on the server rather than by row.)
  useEffect(() => {
    setSelectedKeys(new Set())
    setAllMatching(false)
  }, [resetKey])

  const selectedRows = rows.filter((r) => selectedKeys.has(keyOf(r)))
  const effectiveCount = allMatching ? total : selectedKeys.size
  const allLoadedSelected = rows.length > 0 && selectedRows.length === rows.length
  const canSelectAllMatching = !allMatching && allLoadedSelected && total > rows.length

  function clear() {
    setSelectedKeys(new Set())
    setAllMatching(false)
  }

  function toggleRow(row: Row) {
    const key = keyOf(row)
    setSelectedKeys((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
    // Ticking individual rows steps out of the "all matching" selection.
    setAllMatching(false)
  }

  function toggleAllLoaded() {
    setAllMatching(false)
    setSelectedKeys((prev) => {
      // If every loaded row is already selected, clear; otherwise select them all.
      if (prev.size >= rows.length && rows.every((r) => prev.has(keyOf(r)))) {
        return new Set()
      }
      return new Set(rows.map(keyOf))
    })
  }

  return {
    selectedKeys,
    allMatching,
    selectedRows,
    effectiveCount,
    canSelectAllMatching,
    keyOf,
    isSelected: (row) => selectedKeys.has(keyOf(row)),
    toggleRow,
    toggleAllLoaded,
    selectAllMatching: () => setAllMatching(true),
    clear,
  }
}
