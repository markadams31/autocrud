/**
 * filter-bar.tsx — Per-column filters above the grid.
 *
 * "Add filter" lists the columns not already filtered; each active filter is a
 * chip with three parts: the column name, an operator, and a type-aware value
 * editor. Operators go well beyond equality — comparisons and ranges on numbers
 * and dates, contains/starts/ends on text, and is-empty/is-not-empty anywhere —
 * with the operator set offered per column type. Values are raw strings here
 * (URL-friendly); table-view coerces them to the column's type, drops incomplete
 * filters, and debounces before querying.
 */

import { ListFilterIcon, XIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useOptions } from '@/hooks/queries'
import type { ColumnFilter } from '@/hooks/use-url-state'
import { fieldLabel } from '@/lib/format'
import type { ColumnMeta, FilterOp } from '@/types'

interface FilterBarProps {
  columns: ColumnMeta[]
  filters: Record<string, ColumnFilter>
  onChange: (filters: Record<string, ColumnFilter>) => void
  schema: string
  table: string
}

type OpChoice = { value: FilterOp; label: string }

const EMPTY_OPS: OpChoice[] = [
  { value: 'isnull', label: 'is empty' },
  { value: 'notnull', label: 'is not empty' },
]

/** The operators offered for a column, by type. The first is the default. */
function opsFor(column: ColumnMeta): OpChoice[] {
  if (column.foreign_key) {
    return [{ value: 'eq', label: 'is' }, ...EMPTY_OPS]
  }
  switch (column.field_type) {
    case 'boolean':
      return [{ value: 'eq', label: 'is' }, ...(column.nullable ? EMPTY_OPS : [])]
    case 'integer':
    case 'number':
    case 'decimal':
      return [
        { value: 'eq', label: '=' },
        { value: 'ne', label: '≠' },
        { value: 'gt', label: '>' },
        { value: 'gte', label: '≥' },
        { value: 'lt', label: '<' },
        { value: 'lte', label: '≤' },
        { value: 'between', label: 'between' },
        ...EMPTY_OPS,
      ]
    case 'date':
    case 'datetime':
    case 'time':
      return [
        { value: 'eq', label: 'on' },
        { value: 'lt', label: 'before' },
        { value: 'gt', label: 'after' },
        { value: 'between', label: 'between' },
        ...EMPTY_OPS,
      ]
    default: // text
      return [
        { value: 'contains', label: 'contains' },
        { value: 'eq', label: 'is' },
        { value: 'startswith', label: 'starts with' },
        { value: 'endswith', label: 'ends with' },
        ...EMPTY_OPS,
      ]
  }
}

function defaultOp(column: ColumnMeta): FilterOp {
  if (!column.foreign_key && column.field_type === 'text') return 'contains'
  return 'eq'
}

/** Native input type for a column's value editor. */
function valueInputType(column: ColumnMeta): string {
  switch (column.field_type) {
    case 'date':
    case 'datetime':
      return 'date'
    case 'time':
      return 'time'
    case 'integer':
    case 'number':
    case 'decimal':
      return 'number'
    default:
      return 'text'
  }
}

function needsValue(op: FilterOp): boolean {
  return op !== 'isnull' && op !== 'notnull'
}

const inputClass =
  'h-6 w-24 rounded-md border-0 bg-transparent px-1 py-0 text-xs shadow-none focus-visible:ring-0'
const rangeInputClass = inputClass.replace('w-24', 'w-20')

function FilterValueControl({
  column,
  filter,
  schema,
  table,
  onChange,
}: {
  column: ColumnMeta
  filter: ColumnFilter
  schema: string
  table: string
  onChange: (filter: ColumnFilter) => void
}) {
  const { op, value } = filter
  const isForeignKey = Boolean(column.foreign_key)
  // FK options are only needed for the "is" (eq) editor.
  const { data: options } = useOptions(
    schema,
    table,
    isForeignKey && op === 'eq' ? column.name : null,
  )

  // Equality on a foreign key or boolean → a dropdown of known values.
  if (op === 'eq' && (isForeignKey || column.field_type === 'boolean')) {
    const items =
      column.field_type === 'boolean'
        ? [
            { value: 'true', label: 'Yes' },
            { value: 'false', label: 'No' },
          ]
        : (options ?? []).map((o) => ({ value: String(o.value), label: o.label }))

    return (
      <Select
        items={items}
        value={value || null}
        onValueChange={(v) => onChange({ op, value: v ?? '' })}
      >
        <SelectTrigger
          size="sm"
          className="h-6 min-w-24 gap-1 border-0 bg-transparent px-1 text-xs shadow-none focus-visible:ring-0"
        >
          <SelectValue placeholder="any" />
        </SelectTrigger>
        <SelectContent>
          {items.map((item) => (
            <SelectItem key={item.value} value={item.value}>
              {item.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    )
  }

  const type = valueInputType(column)

  // Range → a low/high pair, stored as "low..high".
  if (op === 'between') {
    const [low = '', high = ''] = value.split('..')
    return (
      <span className="flex items-center gap-1">
        <Input
          type={type}
          value={low}
          aria-label={`${fieldLabel(column)} filter from`}
          onChange={(e) => onChange({ op, value: `${e.target.value}..${high}` })}
          className={rangeInputClass}
        />
        <span className="text-muted-foreground">and</span>
        <Input
          type={type}
          value={high}
          aria-label={`${fieldLabel(column)} filter to`}
          onChange={(e) => onChange({ op, value: `${low}..${e.target.value}` })}
          className={rangeInputClass}
        />
      </span>
    )
  }

  return (
    <Input
      type={type}
      value={value}
      placeholder="value"
      aria-label={`${fieldLabel(column)} filter value`}
      onChange={(e) => onChange({ op, value: e.target.value })}
      className={inputClass}
    />
  )
}

function FilterChip({
  column,
  filter,
  schema,
  table,
  onChange,
  onRemove,
}: {
  column: ColumnMeta
  filter: ColumnFilter
  schema: string
  table: string
  onChange: (filter: ColumnFilter) => void
  onRemove: () => void
}) {
  const ops = opsFor(column)

  function changeOp(op: FilterOp) {
    // Reset the value when switching to a valueless op, or in/out of a range,
    // since the value's shape no longer applies.
    const shapeChanged = (op === 'between') !== (filter.op === 'between')
    const value = !needsValue(op) || shapeChanged ? '' : filter.value
    onChange({ op, value })
  }

  return (
    <div className="flex items-center gap-1 rounded-lg border bg-card py-0.5 pr-0.5 pl-2.5 text-xs shadow-sm">
      <span className="font-medium text-foreground/80">{fieldLabel(column)}</span>
      <Select items={ops} value={filter.op} onValueChange={(v) => changeOp(v as FilterOp)}>
        <SelectTrigger
          size="sm"
          aria-label={`${fieldLabel(column)} operator`}
          className="h-6 gap-1 border-0 bg-transparent px-1 text-xs text-muted-foreground shadow-none focus-visible:ring-0"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {ops.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {needsValue(filter.op) && (
        <FilterValueControl
          column={column}
          filter={filter}
          schema={schema}
          table={table}
          onChange={onChange}
        />
      )}
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove ${fieldLabel(column)} filter`}
        className="rounded p-1 text-muted-foreground transition-colors outline-none hover:bg-muted hover:text-destructive focus-visible:ring-[3px] focus-visible:ring-ring/50"
      >
        <XIcon className="size-3" />
      </button>
    </div>
  )
}

export function FilterBar({ columns, filters, onChange, schema, table }: FilterBarProps) {
  const active = columns.filter((c) => c.name in filters)
  const available = columns.filter((c) => !(c.name in filters))

  return (
    <div className="flex flex-wrap items-center gap-2 duration-300 animate-in fade-in slide-in-from-top-1">
      {available.length > 0 && (
        <DropdownMenu>
          <DropdownMenuTrigger render={<Button variant="outline" size="sm" />}>
            <ListFilterIcon />
            Add filter
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="max-h-72 overflow-y-auto">
            {available.map((column) => (
              <DropdownMenuItem
                key={column.name}
                onClick={() =>
                  onChange({ ...filters, [column.name]: { op: defaultOp(column), value: '' } })
                }
              >
                {fieldLabel(column)}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      )}

      {active.map((column) => (
        <FilterChip
          key={column.name}
          column={column}
          filter={filters[column.name]}
          schema={schema}
          table={table}
          onChange={(filter) => onChange({ ...filters, [column.name]: filter })}
          onRemove={() => {
            const next = { ...filters }
            delete next[column.name]
            onChange(next)
          }}
        />
      ))}

      {active.length > 0 && (
        <Button
          variant="ghost"
          size="sm"
          className="text-muted-foreground"
          onClick={() => onChange({})}
        >
          Clear all
        </Button>
      )}
    </div>
  )
}
