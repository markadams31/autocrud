/**
 * field-control.tsx — Renders the right input for a column, driven by metadata.
 *
 * The column's field_type and foreign_key decide the control: a FK becomes a
 * searchable dropdown of its target's display labels, a boolean becomes a
 * switch, dates get native pickers, long text gets a textarea, and so on. No
 * column is special-cased by name — everything comes from the reflected schema.
 */

import {
  Combobox,
  ComboboxActions,
  ComboboxClear,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxInputGroup,
  ComboboxItem,
  ComboboxList,
  ComboboxTrigger,
} from '@/components/ui/combobox'
import { Input } from '@/components/ui/input'
import { NumberField } from '@/components/ui/number-field'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { useOptions } from '@/hooks/queries'
import { toDateInput, toDateTimeLocal, toTimeInput } from '@/lib/format'
import type { ColumnMeta } from '@/types'

export interface FieldControlProps {
  column: ColumnMeta
  value: unknown
  onChange: (value: unknown) => void
  schema: string
  table: string
  invalid?: boolean
  autoFocus?: boolean
  id?: string
}

// Long-text heuristic: NVARCHAR(MAX) reflects with no length; anything beyond a
// single line's worth of characters reads better in a textarea.
function isLongText(column: ColumnMeta): boolean {
  return column.field_type === 'text' && (column.max_length == null || column.max_length > 512)
}

// Integers display grouped with no fraction. Floats allow many fraction digits
// so the formatter never rounds a typed value away (the default caps at 3).
const INTEGER_FORMAT: Intl.NumberFormatOptions = { maximumFractionDigits: 0 }
const FLOAT_FORMAT: Intl.NumberFormatOptions = { maximumFractionDigits: 20 }

/** Coerce a stored form value (string | null) to the number a NumberField wants. */
function toNumber(value: unknown): number | null {
  if (value == null || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

interface ComboItem {
  value: string
  label: string
}

/**
 * A foreign key's value is chosen from the referenced table's display labels.
 * Once a table has more than a handful of rows a plain dropdown is unusable, so
 * this is a searchable combobox: the user types to filter by label, and the raw
 * id is what gets stored. Nullable columns get a clear button.
 */
function ForeignKeySelect({
  column,
  value,
  onChange,
  schema,
  table,
  invalid,
  autoFocus,
  id,
}: FieldControlProps) {
  const { data, isLoading } = useOptions(schema, table, column.name)

  const items: ComboItem[] = (data ?? []).map((o) => ({
    value: String(o.value),
    label: o.label,
  }))

  const current =
    value == null || value === ''
      ? null
      : (items.find((i) => i.value === String(value)) ?? null)

  return (
    <Combobox
      items={items}
      value={current}
      onValueChange={(item: ComboItem | null) => onChange(item ? item.value : null)}
      isItemEqualToValue={(a: ComboItem, b: ComboItem) => a.value === b.value}
      disabled={isLoading}
    >
      <ComboboxInputGroup>
        <ComboboxInput
          id={id}
          autoFocus={autoFocus}
          aria-invalid={invalid}
          placeholder={isLoading ? 'Loading…' : 'Search…'}
        />
        <ComboboxActions>
          {column.nullable && current && <ComboboxClear />}
          <ComboboxTrigger />
        </ComboboxActions>
      </ComboboxInputGroup>
      <ComboboxContent>
        <ComboboxEmpty>No matches.</ComboboxEmpty>
        <ComboboxList>
          {(item: ComboItem) => (
            <ComboboxItem key={item.value} value={item}>
              {item.label}
            </ComboboxItem>
          )}
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  )
}

export function FieldControl(props: FieldControlProps) {
  const { column, value, onChange, invalid, autoFocus, id } = props

  if (column.foreign_key) {
    return <ForeignKeySelect {...props} />
  }

  switch (column.field_type) {
    case 'boolean':
      return (
        <Switch
          id={id}
          checked={value === true}
          onCheckedChange={(checked) => onChange(checked)}
        />
      )

    case 'date':
      return (
        <Input
          id={id}
          type="date"
          autoFocus={autoFocus}
          aria-invalid={invalid}
          value={toDateInput(value)}
          onChange={(e) => onChange(e.target.value || null)}
        />
      )

    case 'datetime':
      return (
        <Input
          id={id}
          type="datetime-local"
          autoFocus={autoFocus}
          aria-invalid={invalid}
          value={toDateTimeLocal(value)}
          onChange={(e) => onChange(e.target.value || null)}
        />
      )

    case 'time':
      return (
        <Input
          id={id}
          type="time"
          step={1}
          autoFocus={autoFocus}
          aria-invalid={invalid}
          value={toTimeInput(value)}
          onChange={(e) => onChange(e.target.value || null)}
        />
      )

    case 'integer':
      return (
        <NumberField
          id={id}
          step={1}
          format={INTEGER_FORMAT}
          autoFocus={autoFocus}
          invalid={invalid}
          value={toNumber(value)}
          // Keep form state as strings (like every other control) so the
          // form's change-detection and conversion stay uniform.
          onValueChange={(n) => onChange(n == null ? null : String(n))}
        />
      )

    case 'number':
      return (
        <NumberField
          id={id}
          step="any"
          format={FLOAT_FORMAT}
          autoFocus={autoFocus}
          invalid={invalid}
          value={toNumber(value)}
          onValueChange={(n) => onChange(n == null ? null : String(n))}
        />
      )

    case 'decimal':
      // Kept as text + decimal keypad so precision is never lost to float.
      return (
        <Input
          id={id}
          type="text"
          inputMode="decimal"
          autoFocus={autoFocus}
          aria-invalid={invalid}
          value={value == null ? '' : String(value)}
          onChange={(e) => onChange(e.target.value === '' ? null : e.target.value)}
        />
      )

    default:
      if (isLongText(column)) {
        return (
          <Textarea
            id={id}
            rows={3}
            // field-sizing-content (Tailwind v4) grows the textarea with its
            // content, capped so a long note doesn't take over the form.
            className="field-sizing-content max-h-64"
            autoFocus={autoFocus}
            aria-invalid={invalid}
            maxLength={column.max_length ?? undefined}
            value={value == null ? '' : String(value)}
            onChange={(e) => onChange(e.target.value === '' ? null : e.target.value)}
          />
        )
      }
      return (
        <Input
          id={id}
          type="text"
          autoFocus={autoFocus}
          aria-invalid={invalid}
          maxLength={column.max_length ?? undefined}
          value={value == null ? '' : String(value)}
          onChange={(e) => onChange(e.target.value === '' ? null : e.target.value)}
        />
      )
  }
}
