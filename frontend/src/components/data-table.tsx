/**
 * data-table.tsx — The metadata-driven grid shell (presentational).
 *
 * Owns no fetching — it receives rows, sort state, and resolved FK labels and
 * lays them out. This file owns the grid *shell*: fixed column widths, the
 * single scroll surface, sticky header/actions, selection, row enter/exit
 * animation, and inline-edit orchestration. What goes *inside* a cell — the
 * value renderer, the column header, the in-place editor, the FK hover preview —
 * lives in data-table-cells.tsx and foreign-key-cell.tsx.
 *
 * Layout: a single scroll surface (both axes) with a sticky header and a sticky
 * right-hand actions column, so wide tables scroll horizontally while the header
 * and row controls stay put. Column headers sort; row actions sharpen on hover.
 * While loading, the body fills with synchronized shimmer rows that match the
 * real column layout.
 */

import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { EyeIcon, PencilIcon } from 'lucide-react'

import { AnimatedTrash } from '@/components/animated-trash'
import { Cell, EditableCell, HeaderCell, type SortState } from '@/components/data-table-cells'
import {
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import type { FkLabelMap } from '@/hooks/queries'
import type { Density } from '@/hooks/use-table-prefs'
import { easeOutExpo } from '@/lib/animations'
import { convertFieldValue, toInputValue } from '@/lib/field-values'
import { isNumericColumn } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { ColumnMeta, Row, TablePermissions } from '@/types'

interface DataTableProps {
  columns: ColumnMeta[]
  rows: Row[]
  fkLabels: FkLabelMap
  sort: SortState
  onSort: (column: string) => void
  loading: boolean
  skeletonRows?: number
  density?: Density
  permissions: TablePermissions
  onRowClick: (row: Row) => void
  onEdit: (row: Row) => void
  onDelete: (row: Row) => void
  /** Row selection (for bulk actions). Omitted when the user can't delete. */
  selection?: {
    isSelected: (row: Row) => boolean
    onToggleRow: (row: Row) => void
    onToggleAllLoaded: () => void
  }
  /** True for a row that was just created/updated — it briefly flashes. */
  recentlyChanged?: (row: Row) => boolean
  /** Schema/table of the current grid — needed by the inline cell editor's FK lookups. */
  schema: string
  table: string
  /**
   * Commit an inline cell edit. When provided (and the user can update), an
   * editable, non-FK cell can be double-clicked to edit in place. FK columns
   * still edit via the slide-over.
   */
  onCellEdit?: (row: Row, column: ColumnMeta, value: unknown) => void
}

// Shared classes for the sticky actions column so header and body stay aligned.
const STICKY_ACTION = 'sticky right-0 z-10'
// Width (px) of the leading selection checkbox column.
const SELECT_COL_WIDTH = 44

// Fixed column width (px) derived from the column's type — not its content.
// Used with `table-layout: fixed` so widths are identical whether the body
// holds loading skeletons, page 1, or page 50; nothing reflows as data arrives.
function columnWidth(col: ColumnMeta): number {
  if (col.foreign_key) return 176
  switch (col.field_type) {
    case 'boolean':
      return 96
    case 'integer':
      return col.is_primary_key ? 96 : 112
    case 'number':
    case 'decimal':
      return 128
    case 'date':
      return 132
    case 'time':
      return 112
    case 'datetime':
      return 184
    default: {
      // text — scale to the declared max length; NVARCHAR(MAX) gets the widest.
      if (col.max_length == null || col.max_length > 512) return 320
      if (col.max_length <= 16) return 120
      if (col.max_length <= 48) return 176
      return 240
    }
  }
}

export function DataTable({
  columns,
  rows,
  fkLabels,
  sort,
  onSort,
  loading,
  skeletonRows = 10,
  density = 'comfortable',
  permissions,
  onRowClick,
  onEdit,
  onDelete,
  selection,
  recentlyChanged,
  schema,
  table,
  onCellEdit,
}: DataTableProps) {
  // The actions column is always present: every row offers a keyboard-reachable
  // "View" control (opening the read-only detail), so that action isn't available
  // by mouse-only row click alone. Edit/Delete are added when the user is allowed.
  const showActions = true
  // Row padding follows the density preference.
  const densityPad = density === 'compact' ? 'py-1' : 'py-2.5'

  // ── Inline cell editing ────────────────────────────────────────────────────
  const inlineEnabled = permissions.update && Boolean(onCellEdit)
  const [editing, setEditing] = useState<{ rowKey: string; column: string } | null>(null)
  // A single click opens the row's panel; when inline editing is on we delay it
  // briefly so a double-click (to edit a cell) can cancel the open.
  const clickTimer = useRef<number | undefined>(undefined)

  const isInlineEditable = (column: ColumnMeta) =>
    inlineEnabled && column.editable && !column.foreign_key

  function handleRowOpen(row: Row) {
    if (editing) return
    if (!inlineEnabled) {
      onRowClick(row)
      return
    }
    window.clearTimeout(clickTimer.current)
    clickTimer.current = window.setTimeout(() => onRowClick(row), 200)
  }

  function startEdit(e: React.MouseEvent, key: string, column: ColumnMeta) {
    e.stopPropagation()
    window.clearTimeout(clickTimer.current)
    setEditing({ rowKey: key, column: column.name })
  }

  function commitEdit(row: Row, column: ColumnMeta, value: unknown) {
    setEditing(null)
    // Skip a no-op write — only fire when the value actually changed.
    if (value !== convertFieldValue(column, toInputValue(column, row))) {
      onCellEdit?.(row, column, value)
    }
  }

  // Selection header state, derived from the rows currently loaded on this page.
  const selectedOnPage = selection ? rows.filter(selection.isSelected).length : 0
  const allLoadedSelected = selectedOnPage > 0 && selectedOnPage === rows.length
  const someLoadedSelected = selectedOnPage > 0 && selectedOnPage < rows.length

  // Reconcile rows by primary key so React updates/removes the right <tr>
  // (e.g. on optimistic delete) instead of shifting index-keyed rows.
  const pkNames = columns.filter((c) => c.is_primary_key).map((c) => c.name)
  const rowKey = (row: Row, i: number) =>
    pkNames.length ? pkNames.map((k) => String(row[k])).join('') : String(i)

  // Play the staggered reveal on first load only; later page/sort/filter
  // changes get a quick uniform fade so paging never feels heavy.
  const revealedRef = useRef(false)
  useEffect(() => {
    if (!loading && rows.length) revealedRef.current = true
  }, [loading, rows.length])

  // Pre-compute fixed widths so the table never reflows as content changes.
  const widths = columns.map(columnWidth)
  // One column per action button: View (always) + Edit/Delete when allowed.
  const actionWidth =
    (1 + (permissions.update ? 1 : 0) + (permissions.delete ? 1 : 0)) * 34 + 16
  const selectWidth = selection ? SELECT_COL_WIDTH : 0
  const minWidth = widths.reduce((sum, w) => sum + w, 0) + actionWidth + selectWidth

  return (
    <div className="table-scroll h-full overflow-auto">
      <table
        className="w-full table-fixed border-collapse text-sm"
        style={{ minWidth }}
      >
        <colgroup>
          {selection && <col style={{ width: selectWidth }} />}
          {columns.map((c, i) => (
            <col key={c.name} style={{ width: widths[i] }} />
          ))}
          {showActions && <col style={{ width: actionWidth }} />}
        </colgroup>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            {selection && (
              <TableHead className="sticky top-0 z-20 h-9 bg-muted/80 px-0 text-center backdrop-blur-sm shadow-[inset_0_-1px_0_var(--border)]">
                <Checkbox
                  className="mx-auto"
                  aria-label="Select all rows on this page"
                  checked={allLoadedSelected}
                  indeterminate={someLoadedSelected}
                  disabled={rows.length === 0}
                  onCheckedChange={() => selection.onToggleAllLoaded()}
                />
              </TableHead>
            )}
            {columns.map((column) => (
              <HeaderCell key={column.name} column={column} sort={sort} onSort={onSort} />
            ))}
            {showActions && (
              <TableHead
                className={cn(
                  STICKY_ACTION,
                  'top-0 z-30 h-9 w-0 bg-muted/80 backdrop-blur-sm shadow-[inset_1px_-1px_0_var(--border)]',
                )}
                aria-label="Actions"
              />
            )}
          </TableRow>
        </TableHeader>

        <TableBody>
          {loading
            ? Array.from({ length: skeletonRows }).map((_, r) => (
                <TableRow key={`skeleton-${r}`} className="hover:bg-transparent">
                  {selection && (
                    <TableCell className={cn(densityPad, 'text-center')}>
                      <div className="mx-auto size-4 rounded bg-muted" />
                    </TableCell>
                  )}
                  {columns.map((column) => (
                    <TableCell key={column.name} className={densityPad}>
                      <div
                        className="shimmer h-4 rounded bg-muted"
                        style={{ width: `${42 + ((r * 7 + column.name.length * 3) % 44)}%` }}
                      />
                    </TableCell>
                  ))}
                  {showActions && <TableCell className={cn(STICKY_ACTION, 'bg-card')} />}
                </TableRow>
              ))
            : null}
          {!loading && (
            // AnimatePresence keeps a deleted row mounted long enough to play its
            // exit animation before it leaves the DOM. Default `initial` is kept
            // so the first-load stagger reveal below still plays.
            <AnimatePresence>
              {rows.map((row, i) => {
                // Slow→fast cascade on first load (the gap between rows shrinks
                // along a sqrt curve, normalised so the wave lands in ~0.5s);
                // a quick uniform fade afterwards so paging never feels heavy.
                const count = rows.length
                const delay = revealedRef.current
                  ? 0
                  : 0.5 * Math.sqrt(count > 1 ? i / (count - 1) : 0)
                const key = rowKey(row, i)
                const selected = selection?.isSelected(row) ?? false
                const flash = recentlyChanged?.(row) ?? false
                return (
                <motion.tr
                  key={key}
                  layout
                  data-slot="table-row"
                  data-state={selected ? 'selected' : undefined}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  // On delete the row fades/slides out before unmounting; `layout`
                  // then slides the rows below up into the gap.
                  exit={{ opacity: 0, x: -12, transition: { duration: 0.2, ease: easeOutExpo } }}
                  transition={{
                    duration: 0.35,
                    ease: easeOutExpo,
                    delay,
                    layout: { duration: 0.25, ease: easeOutExpo },
                  }}
                  onClick={() => handleRowOpen(row)}
                  className={cn(
                    'group cursor-pointer border-b transition-colors hover:bg-[var(--row-hover)] active:bg-[var(--row-active)] data-[state=selected]:bg-[var(--row-hover)]',
                    flash && 'row-flash',
                  )}
                >
                  {selection && (
                    <TableCell
                      className={cn(
                        densityPad,
                        'text-center transition-colors group-hover:bg-[var(--row-hover)] group-active:bg-[var(--row-active)]',
                      )}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <Checkbox
                        className="mx-auto"
                        aria-label="Select row"
                        checked={selected}
                        onCheckedChange={() => selection.onToggleRow(row)}
                      />
                    </TableCell>
                  )}
                  {columns.map((column) => {
                    const editingThis =
                      editing?.rowKey === key && editing.column === column.name
                    const editable = isInlineEditable(column)
                    return (
                      <TableCell
                        key={column.name}
                        onDoubleClick={editable ? (e) => startEdit(e, key, column) : undefined}
                        onClick={editingThis ? (e) => e.stopPropagation() : undefined}
                        className={cn(
                          densityPad,
                          'tabular-nums transition-colors group-hover:bg-[var(--row-hover)] group-active:bg-[var(--row-active)]',
                          isNumericColumn(column) && 'text-right',
                        )}
                      >
                        {editingThis ? (
                          <EditableCell
                            schema={schema}
                            table={table}
                            column={column}
                            row={row}
                            onCommit={(value) => commitEdit(row, column, value)}
                            onCancel={() => setEditing(null)}
                          />
                        ) : (
                          <Cell column={column} row={row} fkLabels={fkLabels} />
                        )}
                      </TableCell>
                    )
                  })}
                  {showActions && (
                    <TableCell
                      className={cn(
                        STICKY_ACTION,
                        'w-0 bg-card p-1 shadow-[inset_1px_0_0_var(--border)] transition-colors group-hover:bg-[var(--row-hover)] group-active:bg-[var(--row-active)]',
                      )}
                    >
                      {/* Non-interactive wrapper: stops the row-open click from
                          firing when its child buttons are activated. */}
                      {/* eslint-disable-next-line jsx-a11y/no-static-element-interactions, jsx-a11y/click-events-have-key-events */}
                      <div
                        className="flex items-center justify-end gap-0.5 opacity-70 transition-opacity duration-200 group-hover:opacity-100"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Tooltip>
                          <TooltipTrigger
                            render={
                              <Button
                                variant="ghost"
                                size="icon-sm"
                                onClick={() => onRowClick(row)}
                                aria-label="View details"
                              />
                            }
                          >
                            <EyeIcon />
                          </TooltipTrigger>
                          <TooltipContent>View</TooltipContent>
                        </Tooltip>
                        {permissions.update && (
                          <Tooltip>
                            <TooltipTrigger
                              render={
                                <Button
                                  variant="ghost"
                                  size="icon-sm"
                                  onClick={() => onEdit(row)}
                                  aria-label="Edit"
                                />
                              }
                            >
                              <PencilIcon />
                            </TooltipTrigger>
                            <TooltipContent>Edit</TooltipContent>
                          </Tooltip>
                        )}
                        {permissions.delete && (
                          <Tooltip>
                            <TooltipTrigger
                              render={
                                <Button
                                  variant="ghost"
                                  size="icon-sm"
                                  className="text-muted-foreground hover:text-destructive"
                                  onClick={() => onDelete(row)}
                                  aria-label="Delete"
                                />
                              }
                            >
                              <AnimatedTrash />
                            </TooltipTrigger>
                            <TooltipContent>Delete</TooltipContent>
                          </Tooltip>
                        )}
                      </div>
                    </TableCell>
                  )}
                </motion.tr>
                )
              })}
            </AnimatePresence>
          )}
        </TableBody>
      </table>
    </div>
  )
}
