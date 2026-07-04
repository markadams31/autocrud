/**
 * csv-import.ts — Parse, validate, and resolve a CSV for bulk import.
 *
 * Pure functions (no React, no fetch) so the rules are unit-tested directly:
 *   - the template is the editable column names as a header row;
 *   - parsing yields string cells keyed by header (papaparse, BOM-safe);
 *   - analysis maps headers to columns (by exact name, order-independent;
 *     unknown headers ignored, missing required columns flagged), coerces each
 *     cell to its column type, and resolves foreign keys by label *or* id
 *     against the options map the grid already loads.
 *
 * The server is still the final authority (uniqueness, anything the browser
 * can't see); this layer just lets the preview catch the obvious problems and
 * show the user exactly what each value resolved to before they import.
 */

import Papa from 'papaparse'

import type { FkLabelMap } from '@/hooks/queries'
import { csvEscape } from '@/lib/csv'
import { convertFieldValue } from '@/lib/field-values'
import type { ColumnMeta, Row } from '@/types'

/** Editable columns are the only ones a user can import into. */
export function importableColumns(columns: ColumnMeta[]): ColumnMeta[] {
  return columns.filter((c) => c.editable)
}

/** The template body: a single header row of the editable column names. */
export function buildTemplate(columns: ColumnMeta[]): string {
  return importableColumns(columns).map((c) => csvEscape(c.name)).join(',')
}

export interface ParsedCsv {
  headers: string[]
  records: Record<string, string>[]
}

/** Parse CSV text into header names + string-valued row objects. */
export function parseCsv(text: string): ParsedCsv {
  const result = Papa.parse<Record<string, string>>(text.replace(/^﻿/, ''), {
    header: true,
    skipEmptyLines: 'greedy',
    transformHeader: (h) => h.trim(),
  })
  return { headers: result.meta.fields ?? [], records: result.data ?? [] }
}

export interface ImportRow {
  /** 1-based data row number (matches the spreadsheet, header excluded). */
  line: number
  /** Payload to send for this row — only the cells that resolved cleanly. */
  values: Row
  /** Column → error message for this row. */
  errors: Record<string, string>
  /** Column → text shown in the preview (FKs show "Label (id)"). */
  display: Record<string, string>
}

export interface ImportAnalysis {
  /** Editable columns present in the file, in table order (the preview columns). */
  columns: ColumnMeta[]
  rows: ImportRow[]
  /** File headers that aren't editable columns of this table (ignored). */
  unknownHeaders: string[]
  /** Required columns missing from the file's header row entirely. */
  missingRequired: string[]
  /** Total per-cell errors across all rows. */
  errorCount: number
}

/** True when the analysis is safe to import (rows present, nothing wrong). */
export function isImportable(analysis: ImportAnalysis): boolean {
  return (
    analysis.rows.length > 0 &&
    analysis.errorCount === 0 &&
    analysis.missingRequired.length === 0
  )
}

const BOOL_TRUE = new Set(['true', 't', 'yes', 'y', '1'])
const BOOL_FALSE = new Set(['false', 'f', 'no', 'n', '0'])

interface FkResolver {
  byLabel: Map<string, string[]> // lowercased label → ids (a list catches duplicates)
  labelOf: Map<string, string> // id → label
}

function fkResolver(map: Map<string, string>): FkResolver {
  const byLabel = new Map<string, string[]>()
  for (const [id, label] of map) {
    const key = label.trim().toLowerCase()
    const list = byLabel.get(key) ?? []
    list.push(id)
    byLabel.set(key, list)
  }
  return { byLabel, labelOf: map }
}

/** Resolve one FK cell (raw text) to its id, or describe why it can't. */
function resolveFk(resolver: FkResolver, raw: string): { id?: string; display: string; error?: string } {
  // Prefer an exact id match so power users can paste raw keys.
  if (resolver.labelOf.has(raw)) {
    return { id: raw, display: `${resolver.labelOf.get(raw)} (${raw})` }
  }
  const matches = resolver.byLabel.get(raw.toLowerCase())
  if (!matches || matches.length === 0) {
    return { display: raw, error: `No match for “${raw}”.` }
  }
  if (matches.length > 1) {
    return { display: raw, error: `“${raw}” matches more than one row — use the id.` }
  }
  return { id: matches[0], display: `${raw} (${matches[0]})` }
}

const pad2 = (n: number) => String(n).padStart(2, '0')

/**
 * Parse a calendar date in ISO form → "YYYY-MM-DD", or null.
 *
 * Tolerant of what spreadsheets actually emit: dashes or year-first slashes, a
 * missing zero pad ("2025-1-1"), and a trailing time component we ignore
 * ("2025-01-01 00:00:00"). The round-trip check rejects impossible dates like
 * 2025-02-30. Slash day/month formats (01/02/2025) are intentionally NOT parsed
 * here — they're ambiguous; ISO is the agreed input.
 */
function parseIsoDate(raw: string): string | null {
  const m = raw.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[ T].*)?$/)
  if (!m) return null
  const y = Number(m[1])
  const mo = Number(m[2])
  const d = Number(m[3])
  const dt = new Date(Date.UTC(y, mo - 1, d))
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) {
    return null
  }
  return `${y}-${pad2(mo)}-${pad2(d)}`
}

/** Parse a clock time → "HH:MM:SS", or null. Accepts an unpadded hour ("9:30"). */
function parseTime(raw: string): string | null {
  const m = raw.match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?$/)
  if (!m) return null
  const h = Number(m[1])
  const mi = Number(m[2])
  const s = m[3] ? Number(m[3]) : 0
  if (h > 23 || mi > 59 || s > 59) return null
  return `${pad2(h)}:${pad2(mi)}:${pad2(s)}`
}

/** Parse a date-time → "YYYY-MM-DDTHH:MM:SS", or null. Date part as parseIsoDate. */
function parseDateTime(raw: string): string | null {
  const m = raw.match(
    /^(\d{4}[-/]\d{1,2}[-/]\d{1,2})(?:[ T](\d{1,2}:\d{2}(?::\d{2})?))?(?:Z|[+-]\d{2}:?\d{2})?$/,
  )
  if (!m) return null
  const date = parseIsoDate(m[1])
  if (!date) return null
  const time = m[2] ? parseTime(m[2]) : '00:00:00'
  if (!time) return null
  return `${date}T${time}`
}

/** Coerce one non-FK cell to its column's value, or describe why it can't. */
function coerceCell(column: ColumnMeta, raw: string): { value?: unknown; display: string; error?: string } {
  switch (column.field_type) {
    case 'boolean': {
      const low = raw.toLowerCase()
      if (BOOL_TRUE.has(low)) return { value: true, display: 'Yes' }
      if (BOOL_FALSE.has(low)) return { value: false, display: 'No' }
      return { display: raw, error: 'Expected true or false.' }
    }
    case 'integer': {
      const n = Number(raw)
      if (!Number.isFinite(n) || !Number.isInteger(n)) {
        return { display: raw, error: 'Expected a whole number.' }
      }
      return { value: n, display: String(n) }
    }
    case 'number': {
      const n = Number(raw)
      if (!Number.isFinite(n)) return { display: raw, error: 'Expected a number.' }
      return { value: n, display: String(n) }
    }
    case 'decimal': {
      if (!Number.isFinite(Number(raw))) return { display: raw, error: 'Expected a number.' }
      return { value: raw, display: raw } // keep the string to preserve precision
    }
    case 'date': {
      const iso = parseIsoDate(raw)
      if (!iso) return { display: raw, error: 'Expected a date, e.g. 2025-12-31.' }
      return { value: iso, display: iso }
    }
    case 'time': {
      const time = parseTime(raw)
      if (!time) return { display: raw, error: 'Expected a time, e.g. 09:30.' }
      return { value: time, display: time }
    }
    case 'datetime': {
      const dt = parseDateTime(raw)
      if (!dt) return { display: raw, error: 'Expected a date/time, e.g. 2025-12-31 09:30.' }
      return { value: dt, display: dt.replace('T', ' ') }
    }
    default: {
      if (column.max_length != null && raw.length > column.max_length) {
        return { display: raw, error: `Too long (max ${column.max_length}).` }
      }
      return { value: raw, display: raw }
    }
  }
}

/**
 * Validate a parsed CSV against a table's columns and FK options. Every cell is
 * coerced/resolved; a blank cell is omitted (so the database default or NULL
 * applies) unless the column is required.
 */
export function analyzeImport(
  allColumns: ColumnMeta[],
  parsed: ParsedCsv,
  fkLabels: FkLabelMap,
): ImportAnalysis {
  const editable = importableColumns(allColumns)
  const byName = new Map(editable.map((c) => [c.name, c]))
  const headerSet = new Set(parsed.headers)

  const present = editable.filter((c) => headerSet.has(c.name))
  const unknownHeaders = parsed.headers.filter((h) => !byName.has(h))
  const missingRequired = editable
    .filter((c) => c.required && !headerSet.has(c.name))
    .map((c) => c.name)

  const resolvers = new Map<string, FkResolver>()
  for (const c of present) {
    if (c.foreign_key && fkLabels[c.name]) resolvers.set(c.name, fkResolver(fkLabels[c.name]))
  }

  let errorCount = 0
  const rows: ImportRow[] = parsed.records.map((record, i) => {
    const values: Row = {}
    const errors: Record<string, string> = {}
    const display: Record<string, string> = {}

    for (const c of present) {
      const raw = (record[c.name] ?? '').trim()

      if (raw === '') {
        if (c.required) errors[c.name] = 'Required.'
        display[c.name] = ''
        continue // blank → omit, so the DB default / NULL applies
      }

      if (c.foreign_key) {
        const resolver = resolvers.get(c.name)
        if (resolver) {
          const res = resolveFk(resolver, raw)
          display[c.name] = res.display
          if (res.error) errors[c.name] = res.error
          else values[c.name] = convertFieldValue(c, res.id!)
        } else {
          // FK target had no loaded options (e.g. beyond the 1,000 cap):
          // accept the raw value and let the server be the judge.
          display[c.name] = raw
          values[c.name] = convertFieldValue(c, raw)
        }
        continue
      }

      const res = coerceCell(c, raw)
      display[c.name] = res.display
      if (res.error) errors[c.name] = res.error
      else values[c.name] = res.value
    }

    errorCount += Object.keys(errors).length
    return { line: i + 1, values, errors, display }
  })

  return { columns: present, rows, unknownHeaders, missingRequired, errorCount }
}

/** Merge server-reported per-row field errors (the `rows` map) into an analysis. */
export function applyServerRowErrors(
  analysis: ImportAnalysis,
  serverRows: Record<string, Record<string, string>>,
): ImportAnalysis {
  let errorCount = analysis.errorCount
  const rows = analysis.rows.map((row, i) => {
    const extra = serverRows[String(i)]
    if (!extra) return row
    const merged = { ...row.errors }
    for (const [col, msg] of Object.entries(extra)) {
      if (!merged[col]) errorCount += 1
      merged[col] = msg
    }
    return { ...row, errors: merged }
  })
  return { ...analysis, rows, errorCount }
}
