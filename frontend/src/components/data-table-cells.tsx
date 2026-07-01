/**
 * data-table-cells.tsx — How a single grid cell renders.
 *
 * The per-column pieces the grid composes: the sortable column header, the
 * read-only value cell (which renders by field_type — numbers, boolean pills,
 * humanised dates, FK chips, soft-dash nulls), and the in-place editor used for
 * double-click inline editing. The grid shell in data-table.tsx owns layout and
 * row orchestration; this file owns what goes *inside* a cell.
 */

import { useState, type ReactNode } from 'react'
import {
  ArrowDownIcon,
  ArrowUpIcon,
  CalendarClockIcon,
  CalendarIcon,
  ChevronsUpDownIcon,
  ClockIcon,
  HashIcon,
  KeyRoundIcon,
  ToggleLeftIcon,
  TypeIcon,
  type LucideIcon,
} from 'lucide-react'

import { FieldControl } from '@/components/field-control'
import { ForeignKeyCell } from '@/components/foreign-key-cell'
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from '@/components/ui/hover-card'
import { TableHead } from '@/components/ui/table'
import type { FkLabelMap } from '@/hooks/queries'
import { convertFieldValue, toInputValue, type FormValue } from '@/lib/field-values'
import { fieldLabel, formatValue, isNumericColumn, NULL_DISPLAY } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { ColumnMeta, FieldType, Row, SortDirection } from '@/types'

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

/**
 * A sortable column header. Clicking cycles the sort for its column; hovering
 * or keyboard-focusing it opens a card with the column's full metadata
 * (ColumnMetaCard). The card replaces the old native `title` tooltip — richer,
 * and keyboard/screen-reader accessible via the base-ui preview-card.
 */
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
      <HoverCard>
        <HoverCardTrigger
          render={
            <button
              type="button"
              onClick={() => onSort(column.name)}
              aria-label={`Sort by ${fieldLabel(column)}`}
              className={cn(
                'group/sort -mx-1 inline-flex max-w-full items-center gap-1 rounded px-1 py-0.5 text-xs font-semibold tracking-wide uppercase transition-colors group-hover/head:text-foreground',
                numeric && 'flex-row-reverse',
                active ? 'text-foreground' : 'text-muted-foreground',
              )}
            />
          }
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
        </HoverCardTrigger>
        <HoverCardContent side="bottom" align="start" className="w-fit min-w-36 max-w-xs p-3">
          <ColumnMetaCard column={column} />
        </HoverCardContent>
      </HoverCard>
    </TableHead>
  )
}

// A small category icon per field type — the one consistent visual anchor in
// the card header (every card gets exactly one, reflecting the kind of data).
const FIELD_TYPE_ICON: Record<FieldType, LucideIcon> = {
  text: TypeIcon,
  integer: HashIcon,
  number: HashIcon,
  decimal: HashIcon,
  boolean: ToggleLeftIcon,
  date: CalendarIcon,
  datetime: CalendarClockIcon,
  time: ClockIcon,
}

/**
 * The header hover-card body: the column's metadata as ONE consistent
 * label/value grid. Every card shares the same skeleton — a typed headline, a
 * full-bleed divider, then aligned rows in a fixed order (Type → Nullable → Key
 * → References → Managed by) — so the same fact sits in the same place on every
 * column. Rows that don't apply are omitted, never reordered. The card hugs its
 * content (width adapts; capped so long values wrap). Richness is typographic —
 * a per-type icon and monospace for code-like values, not extra formats. Reads
 * entirely from the already-fetched ColumnMeta — no request.
 */
function ColumnMetaCard({ column }: { column: ColumnMeta }) {
  const readOnly = !column.editable
  const fk = column.foreign_key
  const TypeIco = FIELD_TYPE_ICON[column.field_type]
  // Drop the COLLATE clause str(col.type) appends — noise that blew out the card.
  const displayType = (column.sql_type ?? column.field_type).replace(/\s+COLLATE\b.*/i, '')
  return (
    <div className="space-y-2.5">
      <div className="flex items-center gap-2">
        <TypeIco className="size-3.5 shrink-0 text-muted-foreground" />
        <p className="font-heading min-w-0 truncate text-sm font-semibold">{column.name}</p>
      </div>
      <div className="-mx-3 h-px bg-border" />
      <dl className="grid grid-cols-[auto_auto] gap-x-6 gap-y-1.5 text-xs">
        <MetaRow label="Type">
          <span className="font-mono">{displayType}</span>
        </MetaRow>
        <MetaRow label="Nullable">
          {column.nullable ? (
            'Yes'
          ) : (
            <>
              No{column.required && <span className="text-muted-foreground"> · required</span>}
            </>
          )}
        </MetaRow>
        {(column.is_primary_key || fk) && (
          <MetaRow label="Key">{column.is_primary_key ? 'Primary key' : 'Foreign key'}</MetaRow>
        )}
        {fk && (
          <MetaRow label="References">
            <span className="font-mono break-all">{`${fk.schema}.${fk.table}.${fk.column}`}</span>
          </MetaRow>
        )}
        {readOnly && (
          <MetaRow label="Managed by">
            {column.is_audit ? 'the database (audit)' : 'the database'}
          </MetaRow>
        )}
      </dl>
    </div>
  )
}

/** One label/value pair — a dt/dd that flow directly into ColumnMetaCard's grid. */
function MetaRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 font-medium break-words">{children}</dd>
    </>
  )
}
