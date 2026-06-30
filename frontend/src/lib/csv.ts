/**
 * csv.ts — Export rows to a CSV file, matching what the grid displays.
 *
 * Uses the same formatters as the table (local dates, Yes/No, resolved FK
 * labels) so the export reads like the screen, and only includes the columns
 * currently visible. RFC-4180 quoting; UTF-8 BOM so Excel detects the encoding.
 */

import type { FkLabelMap } from '@/hooks/queries'
import { formatValue } from '@/lib/format'
import type { ColumnMeta, Row } from '@/types'

// Leading characters a spreadsheet (Excel, Google Sheets, LibreOffice) treats as
// the start of a formula. A cell beginning with one of these can execute when the
// file is opened — CSV formula injection (CWE-1236). Since this tool exports
// arbitrary, user-writable database content, every exported cell is screened.
const FORMULA_TRIGGERS = /^[=+\-@\t\r]/

/**
 * Neutralise a cell that a spreadsheet would interpret as a formula by prefixing
 * a single quote, so it renders as literal text. A leading +/- on an otherwise
 * numeric cell (e.g. "-5", "+3.14", "-2e3") is a normal number, not a formula,
 * and is left untouched so legitimate negative/signed numbers export cleanly.
 */
function neutralizeFormula(value: string): string {
  if (!FORMULA_TRIGGERS.test(value)) return value
  if (/^[+-]?\d/.test(value) && !Number.isNaN(Number(value))) return value
  return `'${value}`
}

/**
 * Quote a CSV field per RFC 4180 when it contains a quote, comma, or newline,
 * after neutralising any leading formula trigger (see neutralizeFormula).
 */
export function csvEscape(value: string): string {
  const guarded = neutralizeFormula(value)
  return /[",\r\n]/.test(guarded) ? `"${guarded.replace(/"/g, '""')}"` : guarded
}

function cellText(row: Row, column: ColumnMeta, fkLabels: FkLabelMap): string {
  const value = row[column.name]
  if (value === null || value === undefined) return ''
  if (column.foreign_key) {
    return fkLabels[column.name]?.get(String(value)) ?? String(value)
  }
  if (column.field_type === 'boolean') return value ? 'Yes' : 'No'
  return formatValue(value, column) ?? String(value)
}

export function toCsv(columns: ColumnMeta[], rows: Row[], fkLabels: FkLabelMap): string {
  const header = columns.map((c) => csvEscape(c.name)).join(',')
  const body = rows.map((row) =>
    columns.map((c) => csvEscape(cellText(row, c, fkLabels))).join(','),
  )
  return [header, ...body].join('\r\n')
}

export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}
