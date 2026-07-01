/**
 * data-table-cells.tsx — How a single grid cell renders.
 *
 * The per-column pieces the grid composes: the sortable column header, the
 * read-only value cell (which renders by field_type — numbers, boolean pills,
 * humanised dates, FK chips, soft-dash nulls), and the in-place editor used for
 * double-click inline editing. The grid shell in data-table.tsx owns layout and
 * row orchestration; this file owns what goes *inside* a cell.
 */

import { useState } from 'react'
import {
  ArrowDownIcon,
  ArrowUpIcon,
  ChevronsUpDownIcon,
  KeyRoundIcon,
} from 'lucide-react'

import { FieldControl } from '@/components/field-control'
import { ForeignKeyCell } from '@/components/foreign-key-cell'
import { TableHead } from '@/components/ui/table'
import type { FkLabelMap } from '@/hooks/queries'
import { convertFieldValue, toInputValue, type FormValue } from '@/lib/field-values'
import { fieldLabel, formatValue, isNumericColumn, NULL_DISPLAY } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { ColumnMeta, Row, SortDirection } from '@/types'

/** The grid's active sort: which column, which direction. */
export interface SortState {
  column: string
  direction: SortDirection
}

/**
 * In-place editor for a single cell. Reuses FieldControl so the editor matches
 * the slide-over form. Booleans commit on toggle; other types commit on Enter or
 * when focus leaves the cell, and Escape cancels. (FK columns aren't edited here
 * — their combobox popup portals outside the cell, which fights focus tracking —
 * so they fall back to the slide-over.)
 */
export function EditableCell({
  schema,
  table,
  column,
  row,
  onCommit,
  onCancel,
}: {
  schema: string
  table: string
  column: ColumnMeta
  row: Row
  onCommit: (value: unknown) => void
  onCancel: () => void
}) {
  const [draft, setDraft] = useState<FormValue>(() => toInputValue(column, row))
  const commitOnChange = column.field_type === 'boolean'

  return (
    // Keyboard coordinator around the focusable FieldControl input (Enter commits,
    // Escape cancels, blur commits) — not itself an interactive control.
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions
    <div
      className="-my-1"
      onKeyDown={(e) => {
        if (e.key === 'Enter' && !commitOnChange) {
          e.preventDefault()
          onCommit(convertFieldValue(column, draft))
        } else if (e.key === 'Escape') {
          e.preventDefault()
          onCancel()
        }
      }}
      onBlur={(e) => {
        // Ignore focus moving between parts of the same control (e.g. a number
        // field's steppers); only act when focus truly leaves the cell.
        if (e.currentTarget.contains(e.relatedTarget as Node | null)) return
        if (commitOnChange) onCancel()
        else onCommit(convertFieldValue(column, draft))
      }}
    >
      <FieldControl
        column={column}
        value={draft}
        onChange={(v) => {
          const next = v as FormValue
          setDraft(next)
          if (commitOnChange) onCommit(convertFieldValue(column, next))
        }}
        schema={schema}
        table={table}
        autoFocus
      />
    </div>
  )
}

/** A read-only value cell, rendered by the column's field_type. */
export function Cell({
  column,
  row,
  fkLabels,
}: {
  column: ColumnMeta
  row: Row
  fkLabels: FkLabelMap
}) {
  const value = row[column.name]

  // Foreign key — show the resolved display label, fall back to the raw id,
  // and preview the referenced record on hover.
  if (column.foreign_key) {
    if (value == null) return <span className="text-muted-foreground/40">{NULL_DISPLAY}</span>
    const label = fkLabels[column.name]?.get(String(value)) ?? String(value)
    return <ForeignKeyCell fk={column.foreign_key} value={value} label={label} />
  }

  if (column.field_type === 'boolean') {
    if (value == null) return <span className="text-muted-foreground/40">{NULL_DISPLAY}</span>
    return (
      <span
        className={cn(
          'inline-flex items-center gap-1.5 text-xs font-medium',
          value ? 'text-foreground' : 'text-muted-foreground',
        )}
      >
        <span
          className={cn('size-1.5 rounded-full', value ? 'bg-success' : 'bg-muted-foreground/40')}
        />
        {value ? 'Yes' : 'No'}
      </span>
    )
  }

  const formatted = formatValue(value, column)
  if (formatted == null) {
    return <span className="text-muted-foreground/40">{NULL_DISPLAY}</span>
  }
  return (
    <span
      className={cn('block max-w-full truncate', column.is_audit && 'text-muted-foreground')}
      title={formatted}
    >
      {formatted}
    </span>
  )
}

/** A sortable column header; clicking cycles the sort for its column. */
export function HeaderCell({
  column,
  sort,
  onSort,
}: {
  column: ColumnMeta
  sort: SortState
  onSort: (c: string) => void
}) {
  const active = sort.column === column.name
  const Icon = active ? (sort.direction === 'asc' ? ArrowUpIcon : ArrowDownIcon) : ChevronsUpDownIcon
  const numeric = isNumericColumn(column)
  return (
    <TableHead
      className={cn(
        'group/head sticky top-0 z-20 h-9 bg-muted/80 backdrop-blur-sm transition-colors shadow-[inset_0_-1px_0_var(--border)] hover:bg-muted',
        numeric && 'text-right',
      )}
    >
      <button
        type="button"
        onClick={() => onSort(column.name)}
        className={cn(
          'group/sort -mx-1 inline-flex max-w-full items-center gap-1 rounded px-1 py-0.5 text-xs font-semibold tracking-wide uppercase transition-colors group-hover/head:text-foreground',
          numeric && 'flex-row-reverse',
          active ? 'text-foreground' : 'text-muted-foreground',
        )}
        title={`${column.field_type}${column.nullable ? ' · nullable' : ''}${column.foreign_key ? ` · → ${column.foreign_key.table}` : ''}`}
      >
        {column.is_primary_key && <KeyRoundIcon className="size-3 shrink-0 text-muted-foreground" />}
        <span className="truncate">{fieldLabel(column)}</span>
        <Icon
          className={cn(
            'size-3 shrink-0 transition-all duration-150',
            active
              ? 'opacity-100'
              : 'opacity-0 -translate-x-0.5 group-hover/head:translate-x-0 group-hover/head:opacity-60',
          )}
        />
      </button>
    </TableHead>
  )
}
