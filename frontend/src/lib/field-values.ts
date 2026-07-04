/**
 * field-values.ts — Shared conversion between a form control's "input space"
 * and the JSON value the API expects for a column.
 *
 * Both the single-record form and the bulk-edit form hold control values as
 * strings/booleans/null and must turn them into the column's JSON type the same
 * way, so the rules live here once. Keyed entirely off the column's field_type —
 * no column is special-cased by name.
 */

import {
  localInputToUtc,
  shortestFloatRepr,
  toDateInput,
  toDateTimeLocal,
  toTimeInput,
} from '@/lib/format'
import type { ColumnMeta, Row } from '@/types'

/** The shape a form control holds: a string, a boolean (switches), or null. */
export type FormValue = string | boolean | null

/** Neutral starting value for a freshly-shown control (boolean off, else empty). */
export function emptyFieldValue(column: ColumnMeta): FormValue {
  return column.field_type === 'boolean' ? false : null
}

/**
 * The "input space" value for a column of an existing row — what a FieldControl
 * should start with when editing it (a switch's boolean, a date input's
 * yyyy-mm-dd, otherwise the stringified value). Shared by the record form and
 * inline cell editing so both seed their controls identically.
 */
export function toInputValue(column: ColumnMeta, row: Row): FormValue {
  const v = row[column.name]
  if (column.field_type === 'boolean') return v === true
  if (v == null) return null
  switch (column.field_type) {
    case 'date':
      return toDateInput(v)
    case 'datetime':
      return toDateTimeLocal(v)
    case 'time':
      return toTimeInput(v)
    case 'number': {
      // REAL columns are float32, so a value read back carries float32 noise
      // (33.33 → 33.33000183105469). Seed the editor with the shortest decimal
      // that maps to the same float, so the user sees 33.33 — not the noise the
      // edit NumberField would otherwise show (the grid/detail already round it).
      const n = Number(v)
      return Number.isFinite(n) ? String(shortestFloatRepr(n)) : String(v)
    }
    default:
      return String(v)
  }
}

/** Convert an input-space value to the JSON type the API expects for the column. */
export function convertFieldValue(column: ColumnMeta, value: FormValue): unknown {
  if (value === null || value === '') return null
  if (column.field_type === 'boolean') return value === true
  if (column.field_type === 'integer' || column.field_type === 'number') {
    const n = Number(value)
    return Number.isFinite(n) ? n : value
  }
  // datetime is entered in local time; store it as UTC so it round-trips.
  if (column.field_type === 'datetime') return localInputToUtc(String(value))
  // decimal stays a string to preserve precision; text/date/time pass through.
  return value
}
