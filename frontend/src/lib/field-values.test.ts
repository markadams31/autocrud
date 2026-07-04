import { describe, expect, it } from 'vitest'

import { convertFieldValue, emptyFieldValue, toInputValue } from '@/lib/field-values'
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

describe('emptyFieldValue', () => {
  it('is false for a boolean control, null otherwise', () => {
    expect(emptyFieldValue(col('Active', 'boolean'))).toBe(false)
    expect(emptyFieldValue(col('Name', 'text'))).toBeNull()
    expect(emptyFieldValue(col('Qty', 'integer'))).toBeNull()
  })
})

describe('toInputValue', () => {
  const row = (v: unknown): Row => ({ X: v })

  it('coerces a boolean cell to a real boolean (incl. null → false)', () => {
    expect(toInputValue(col('X', 'boolean'), row(true))).toBe(true)
    expect(toInputValue(col('X', 'boolean'), row(false))).toBe(false)
    expect(toInputValue(col('X', 'boolean'), row(null))).toBe(false)
  })

  it('returns null for a null non-boolean cell', () => {
    expect(toInputValue(col('X', 'integer'), row(null))).toBeNull()
  })

  it('stringifies scalars and slices a date cell to yyyy-mm-dd', () => {
    expect(toInputValue(col('X', 'integer'), row(42))).toBe('42')
    expect(toInputValue(col('X', 'text'), row('hi'))).toBe('hi')
    expect(toInputValue(col('X', 'date'), row('2024-03-04T00:00:00'))).toBe('2024-03-04')
  })

  it('seeds a float (REAL) field with the clean value, not float32 read-back noise', () => {
    // A REAL column returns 33.33 as 33.33000183105469; the editor must show 33.33.
    expect(toInputValue(col('X', 'number'), row(33.33000183105469))).toBe('33.33')
    expect(toInputValue(col('X', 'number'), row(Math.fround(12.34)))).toBe('12.34')
    // a genuine float64 (FLOAT column) keeps its precision
    expect(toInputValue(col('X', 'number'), row(3.141592653589793))).toBe('3.141592653589793')
  })
})

describe('convertFieldValue', () => {
  it('maps empty input to null', () => {
    expect(convertFieldValue(col('X', 'text'), '')).toBeNull()
    expect(convertFieldValue(col('X', 'integer'), null)).toBeNull()
  })

  it('coerces booleans and numbers, passing a bad number through untouched', () => {
    expect(convertFieldValue(col('X', 'boolean'), true)).toBe(true)
    expect(convertFieldValue(col('X', 'integer'), '42')).toBe(42)
    expect(convertFieldValue(col('X', 'number'), '3.5')).toBe(3.5)
    // not a number → leave as-is so the server validates/rejects, not silently 0
    expect(convertFieldValue(col('X', 'integer'), 'abc')).toBe('abc')
  })

  it('keeps decimals as strings to preserve precision', () => {
    expect(convertFieldValue(col('X', 'decimal', { scale: 3 }), '12.340')).toBe('12.340')
  })

  it('passes text/date/time through unchanged', () => {
    expect(convertFieldValue(col('X', 'text'), 'hello')).toBe('hello')
    expect(convertFieldValue(col('X', 'date'), '2024-03-04')).toBe('2024-03-04')
    expect(convertFieldValue(col('X', 'time'), '13:45:30')).toBe('13:45:30')
  })
})
