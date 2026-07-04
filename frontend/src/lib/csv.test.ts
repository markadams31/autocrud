import { describe, expect, it } from 'vitest'

import type { FkLabelMap } from '@/hooks/queries'
import { csvEscape, toCsv } from '@/lib/csv'
import type { ColumnMeta, FieldType, Row } from '@/types'

function col(name: string, field_type: FieldType, extra: Partial<ColumnMeta> = {}): ColumnMeta {
  return {
    name,
    field_type,
    nullable: true,
    required: false,
    editable: true,
    is_primary_key: false,
    is_audit: false,
    max_length: null,
    precision: null,
    scale: null,
    foreign_key: null,
    ...extra,
  }
}

describe('csvEscape (RFC 4180)', () => {
  it('leaves plain values untouched', () => {
    expect(csvEscape('plain')).toBe('plain')
    expect(csvEscape('')).toBe('')
  })

  it('quotes and doubles quotes when a field contains a comma, quote, or newline', () => {
    expect(csvEscape('a,b')).toBe('"a,b"')
    expect(csvEscape('has"quote')).toBe('"has""quote"')
    expect(csvEscape('line\nbreak')).toBe('"line\nbreak"')
    expect(csvEscape('cr\rreturn')).toBe('"cr\rreturn"')
  })
})

describe('csvEscape (formula-injection guard, CWE-1236)', () => {
  it('prefixes a single quote on cells starting with a formula trigger', () => {
    expect(csvEscape('=1+1')).toBe("'=1+1")
    expect(csvEscape('@SUM(A1)')).toBe("'@SUM(A1)")
    expect(csvEscape('=cmd|calc')).toBe("'=cmd|calc")
    expect(csvEscape('-2+3+cmd|x')).toBe("'-2+3+cmd|x")
    expect(csvEscape('\tTAB')).toBe("'\tTAB")
  })

  it('combines neutralisation with RFC-4180 quoting when needed', () => {
    // A formula cell that also contains a comma is both prefixed and quoted.
    expect(csvEscape('=HYPERLINK("a","b")')).toBe('"\'=HYPERLINK(""a"",""b"")"')
  })

  it('leaves legitimate signed/negative numbers untouched', () => {
    expect(csvEscape('-5')).toBe('-5')
    expect(csvEscape('+3.14')).toBe('+3.14')
    expect(csvEscape('-2e3')).toBe('-2e3')
  })
})

describe('toCsv', () => {
  const columns: ColumnMeta[] = [
    col('Name', 'text'),
    col('Active', 'boolean'),
    col('DepartmentID', 'integer', {
      foreign_key: { schema: 'dbo', table: 'Department', column: 'DepartmentID' },
    }),
    col('Qty', 'integer'),
  ]
  const fkLabels: FkLabelMap = { DepartmentID: new Map([['2', 'Research']]) }

  it('emits a header row of column names, CRLF-joined', () => {
    const csv = toCsv(columns, [], fkLabels)
    expect(csv).toBe('Name,Active,DepartmentID,Qty')
  })

  it('renders booleans as Yes/No, resolves FK labels, and blanks nulls', () => {
    const rows: Row[] = [
      { Name: 'Ada', Active: true, DepartmentID: 2, Qty: 5 },
      { Name: 'Bob', Active: false, DepartmentID: 99, Qty: null },
    ]
    const csv = toCsv(columns, rows, fkLabels)
    const lines = csv.split('\r\n')
    expect(lines[0]).toBe('Name,Active,DepartmentID,Qty')
    expect(lines[1]).toBe('Ada,Yes,Research,5')
    // Unknown FK id falls back to the raw value; null Qty becomes an empty field.
    expect(lines[2]).toBe('Bob,No,99,')
  })

  it('quotes a cell value that contains a comma', () => {
    const csv = toCsv([col('Name', 'text')], [{ Name: 'Lovelace, Ada' }], {})
    expect(csv.split('\r\n')[1]).toBe('"Lovelace, Ada"')
  })
})
