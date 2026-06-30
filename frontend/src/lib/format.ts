/**
 * format.ts — Presentation helpers for column values.
 *
 * The grid and detail views render raw JSON values returned by the API.
 * These helpers turn them into something a person wants to read: thousands
 * separators on numbers, human dates, a clear null marker, and so on — all
 * keyed off the column's declared field_type so behaviour is consistent
 * everywhere a value is shown.
 */

import type { ColumnMeta, FieldType } from '@/types'

/** Shown wherever a value is null/undefined. A single em dash reads as "empty". */
export const NULL_DISPLAY = '—'

const integerFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 })

function formatNumber(value: unknown, column: ColumnMeta): string {
  const n = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(n)) return String(value)

  // decimal arrives as a string from the API; honour the column's scale so
  // money-like columns render with their fixed number of places.
  if (column.field_type === 'decimal' && column.scale != null) {
    return new Intl.NumberFormat(undefined, {
      minimumFractionDigits: column.scale,
      maximumFractionDigits: column.scale,
    }).format(n)
  }
  if (column.field_type === 'integer') return integerFormat.format(n)
  return new Intl.NumberFormat().format(n)
}

/**
 * Parse a DB datetime as UTC. The backend serialises datetime2 columns (e.g.
 * audit stamps written with SYSUTCDATETIME()) as naive ISO strings with no
 * timezone designator; the browser would otherwise read them as local time and
 * show the raw UTC clock value. Appending 'Z' when no offset is present makes
 * the conversion to the viewer's local timezone correct. DATETIMEOFFSET columns
 * (which carry an offset) surface as text, so they never reach this path.
 */
function parseAsUtc(raw: string): Date {
  const hasZone = /([zZ])$|[+-]\d{2}:?\d{2}$/.test(raw)
  return new Date(hasZone ? raw : `${raw}Z`)
}

function formatDate(value: unknown, type: FieldType): string {
  const raw = String(value)

  if (type === 'time') return raw.slice(0, 8)

  if (type === 'date') {
    // A DATE is a plain calendar date with no time or zone — build it from its
    // parts so it never shifts a day under timezone conversion.
    const m = raw.match(/^(\d{4})-(\d{2})-(\d{2})/)
    const d = m
      ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
      : new Date(raw)
    if (Number.isNaN(d.getTime())) return raw
    return d.toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })
  }

  // datetime — stored as UTC, shown in the viewer's local time.
  const d = parseAsUtc(raw)
  if (Number.isNaN(d.getTime())) return raw
  return d.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * Format a single cell value to a display string. Returns null when the value
 * is null/empty so callers can render a styled placeholder rather than text.
 */
export function formatValue(value: unknown, column: ColumnMeta): string | null {
  if (value === null || value === undefined || value === '') return null

  switch (column.field_type) {
    case 'integer':
    case 'number':
    case 'decimal':
      return formatNumber(value, column)
    case 'date':
    case 'datetime':
    case 'time':
      return formatDate(value, column.field_type)
    case 'boolean':
      return value ? 'Yes' : 'No'
    default:
      return String(value)
  }
}

/** Right-align numeric columns; everything else reads better left-aligned. */
export function isNumericColumn(column: ColumnMeta): boolean {
  return (
    column.field_type === 'integer' ||
    column.field_type === 'number' ||
    column.field_type === 'decimal'
  )
}

// ── Native-input value coercion ──────────────────────────────────────────────
// Convert stored/API values into the exact string shape each native input
// expects. All three are idempotent, so they can be applied to both raw API
// values and already-coerced form values without drift.

/**
 * Shortest decimal that round-trips to the same float, used to seed number
 * editors faithfully. A REAL column is float32: a value read back is a float32
 * widened to float64, so its plain string carries float32 noise (33.33 comes
 * back as "33.33000183105469"). When `n` is exactly a float32 value, return the
 * shortest decimal that still maps to that same float32, so an editor shows
 * 33.33 instead of the noise. A genuine float64 (a FLOAT column) is not a
 * float32 value, so it's returned unchanged and keeps its full precision.
 */
export function shortestFloatRepr(n: number): number {
  if (!Number.isFinite(n) || Math.fround(n) !== n) return n
  for (let precision = 1; precision <= 9; precision++) {
    const candidate = Number(n.toPrecision(precision))
    if (Math.fround(candidate) === n) return candidate
  }
  return n
}

export function toDateInput(value: unknown): string {
  if (value == null || value === '') return ''
  return String(value).slice(0, 10)
}

export function toTimeInput(value: unknown): string {
  if (value == null || value === '') return ''
  return String(value).slice(0, 8)
}

export function toDateTimeLocal(value: unknown): string {
  if (value == null || value === '') return ''
  // Interpret the stored value as UTC, then show the viewer's local wall time
  // in the <input type="datetime-local"> (which always works in local time).
  const d = parseAsUtc(String(value))
  if (Number.isNaN(d.getTime())) return String(value)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/**
 * Inverse of toDateTimeLocal for writes: take a datetime-local value (the
 * viewer's local wall time) and return a naive UTC ISO string for storage, so
 * an edited datetime round-trips to the same instant. Kept naive (no 'Z') so it
 * lands cleanly in a datetime2 column without a tz-aware value reaching pyodbc.
 */
export function localInputToUtc(value: string): string {
  const d = new Date(value) // a datetime-local string parses as local time
  if (Number.isNaN(d.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}` +
    `T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`
  )
}

/** Turn a column name into a human label: "DepartmentName" → "Department Name". */
export function humanizeColumn(name: string): string {
  return name
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2') // camelCase / PascalCase boundary
    .replace(/[_-]+/g, ' ') // snake_case / kebab-case
    .replace(/\bId\b/g, 'ID')
    .replace(/\s+/g, ' ')
    .trim()
}

/**
 * Display label for a column. For foreign keys, drop a trailing "ID"
 * ("Manager ID" → "Manager") — the FK badge already shows what it references,
 * so the suffix is just noise. Non-FK columns (including primary keys) keep it.
 */
export function fieldLabel(column: ColumnMeta): string {
  const label = humanizeColumn(column.name)
  if (column.foreign_key) return label.replace(/\s+I[dD]$/, '')
  return label
}
