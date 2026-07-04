import { describe, expect, it } from 'vitest'

import type { FkLabelMap } from '@/hooks/queries'
import {
  analyzeImport,
  applyServerRowErrors,
  buildTemplate,
  isImportable,
  parseCsv,
} from '@/lib/csv-import'
import type { ColumnMeta, FieldType } from '@/types'

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

// A representative table: identity PK (not editable), required text, optional
// int, a boolean, a nullable FK, and an audit column (not editable).
const COLUMNS: ColumnMeta[] = [
  col('EmployeeID', 'integer', { editable: false, is_primary_key: true }),
  col('FullName', 'text', { required: true, nullable: false, max_length: 100 }),
  col('Salary', 'integer'),
  col('IsActive', 'boolean', { nullable: false }),
  col('DepartmentID', 'integer', {
    foreign_key: { schema: 'dbo', table: 'Department', column: 'DepartmentID' },
  }),
  col('CreatedDate', 'datetime', { editable: false, is_audit: true }),
]

// id → label, with a duplicate label ("Research") to exercise ambiguity.
const FK: FkLabelMap = {
  DepartmentID: new Map([
    ['1', 'Engineering'],
    ['2', 'Research'],
    ['3', 'Research'],
  ]),
}

function analyze(csv: string) {
  return analyzeImport(COLUMNS, parseCsv(csv), FK)
}

describe('buildTemplate', () => {
  it('is the editable column names only, in order', () => {
    expect(buildTemplate(COLUMNS)).toBe('FullName,Salary,IsActive,DepartmentID')
  })
})

describe('parseCsv', () => {
  it('reads headers and rows, tolerating a BOM and blank lines', () => {
    const parsed = parseCsv('﻿FullName,Salary\r\nAda,120\r\n\r\nGrace,128\r\n')
    expect(parsed.headers).toEqual(['FullName', 'Salary'])
    expect(parsed.records).toHaveLength(2)
    expect(parsed.records[0]).toMatchObject({ FullName: 'Ada', Salary: '120' })
  })

  it('handles quoted fields with embedded commas', () => {
    const parsed = parseCsv('FullName,Salary\r\n"Lovelace, Ada",120')
    expect(parsed.records[0].FullName).toBe('Lovelace, Ada')
  })
})

describe('analyzeImport — types & blanks', () => {
  it('coerces each cell to its column type', () => {
    const a = analyze('FullName,Salary,IsActive\r\nAda,120,yes')
    expect(a.errorCount).toBe(0)
    expect(a.rows[0].values).toEqual({ FullName: 'Ada', Salary: 120, IsActive: true })
    expect(isImportable(a)).toBe(true)
  })

  it('omits a blank optional cell so the DB default/NULL applies', () => {
    const a = analyze('FullName,Salary\r\nAda,')
    expect(a.errorCount).toBe(0)
    expect('Salary' in a.rows[0].values).toBe(false)
  })

  it('flags a blank required cell', () => {
    const a = analyze('FullName,Salary\r\n,120')
    expect(a.rows[0].errors.FullName).toBe('Required.')
    expect(isImportable(a)).toBe(false)
  })

  it('rejects a non-integer where an integer is expected', () => {
    const a = analyze('FullName,Salary\r\nAda,12.5')
    expect(a.rows[0].errors.Salary).toMatch(/whole number/)
  })

  it('parses booleans and rejects nonsense', () => {
    const ok = analyze('FullName,IsActive\r\nAda,0')
    expect(ok.rows[0].values.IsActive).toBe(false)
    const bad = analyze('FullName,IsActive\r\nAda,maybe')
    expect(bad.rows[0].errors.IsActive).toMatch(/true or false/)
  })

  it('enforces max length on text', () => {
    const a = analyze(`FullName\r\n${'x'.repeat(101)}`)
    expect(a.rows[0].errors.FullName).toMatch(/Too long/)
  })
})

describe('analyzeImport — headers', () => {
  it('ignores unknown columns and notes them', () => {
    const a = analyze('FullName,Bogus\r\nAda,whatever')
    expect(a.unknownHeaders).toEqual(['Bogus'])
    expect(a.errorCount).toBe(0)
    expect(a.rows[0].values).toEqual({ FullName: 'Ada' })
  })

  it('flags a required column missing from the header row', () => {
    const a = analyze('Salary\r\n120')
    expect(a.missingRequired).toEqual(['FullName'])
    expect(isImportable(a)).toBe(false)
  })

  it('only previews columns present in the file, in table order', () => {
    const a = analyze('Salary,FullName\r\n120,Ada')
    expect(a.columns.map((c) => c.name)).toEqual(['FullName', 'Salary'])
  })
})

describe('analyzeImport — foreign keys', () => {
  it('accepts a raw id', () => {
    const a = analyze('FullName,DepartmentID\r\nAda,1')
    expect(a.errorCount).toBe(0)
    expect(a.rows[0].values.DepartmentID).toBe(1)
    expect(a.rows[0].display.DepartmentID).toBe('Engineering (1)')
  })

  it('resolves a unique label to its id', () => {
    const a = analyze('FullName,DepartmentID\r\nAda,Engineering')
    expect(a.rows[0].values.DepartmentID).toBe(1)
  })

  it('errors on an ambiguous label and asks for the id', () => {
    const a = analyze('FullName,DepartmentID\r\nAda,Research')
    expect(a.rows[0].errors.DepartmentID).toMatch(/more than one/)
    expect('DepartmentID' in a.rows[0].values).toBe(false)
  })

  it('errors on a label with no match', () => {
    const a = analyze('FullName,DepartmentID\r\nAda,Marketing')
    expect(a.rows[0].errors.DepartmentID).toMatch(/No match/)
  })

  it('passes the raw value through when no options are loaded', () => {
    const a = analyzeImport(COLUMNS, parseCsv('FullName,DepartmentID\r\nAda,7'), {})
    expect(a.errorCount).toBe(0)
    expect(a.rows[0].values.DepartmentID).toBe(7)
  })
})

describe('analyzeImport — dates, times, datetimes', () => {
  const DATE_COLS: ColumnMeta[] = [
    col('StartDate', 'date'),
    col('StartTime', 'time'),
    col('CreatedAt', 'datetime'),
  ]
  const analyzeDates = (csv: string) => analyzeImport(DATE_COLS, parseCsv(csv), {})

  it('accepts padded and unpadded ISO dates, normalizing to YYYY-MM-DD', () => {
    const a = analyzeDates('StartDate\r\n2025-01-01\r\n2025-1-1\r\n2025/03/04')
    expect(a.errorCount).toBe(0)
    expect(a.rows.map((r) => r.values.StartDate)).toEqual(['2025-01-01', '2025-01-01', '2025-03-04'])
  })

  it('strips a trailing time from a date cell', () => {
    const a = analyzeDates('StartDate\r\n2025-01-01 00:00:00')
    expect(a.errorCount).toBe(0)
    expect(a.rows[0].values.StartDate).toBe('2025-01-01')
  })

  it('rejects impossible and unparseable dates', () => {
    const a = analyzeDates('StartDate\r\n2025-02-30\r\nnope\r\n2025-13-01')
    expect(a.rows.every((r) => r.errors.StartDate)).toBe(true)
  })

  it('accepts an unpadded hour in a time, normalizing to HH:MM:SS', () => {
    const a = analyzeDates('StartTime\r\n9:30\r\n09:30:45')
    expect(a.errorCount).toBe(0)
    expect(a.rows.map((r) => r.values.StartTime)).toEqual(['09:30:00', '09:30:45'])
    expect(analyzeDates('StartTime\r\n25:00').rows[0].errors.StartTime).toBeTruthy()
  })

  it('accepts ISO datetimes with a space or T separator', () => {
    const a = analyzeDates('CreatedAt\r\n2025-01-01 10:00\r\n2025-01-01T10:00:00')
    expect(a.errorCount).toBe(0)
    expect(a.rows.map((r) => r.values.CreatedAt)).toEqual([
      '2025-01-01T10:00:00',
      '2025-01-01T10:00:00',
    ])
  })
})

describe('applyServerRowErrors', () => {
  it('merges per-row field errors and bumps the count', () => {
    const a = analyze('FullName,Salary\r\nAda,120\r\nGrace,128')
    expect(a.errorCount).toBe(0)
    const merged = applyServerRowErrors(a, { '1': { Salary: 'Already taken.' } })
    expect(merged.errorCount).toBe(1)
    expect(merged.rows[1].errors.Salary).toBe('Already taken.')
    expect(isImportable(merged)).toBe(false)
  })
})
