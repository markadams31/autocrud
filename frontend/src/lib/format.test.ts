import { describe, expect, it } from 'vitest'

import {
  fieldLabel,
  formatValue,
  humanizeColumn,
  isNumericColumn,
  localInputToUtc,
  NULL_DISPLAY,
  shortestFloatRepr,
  toDateInput,
  toDateTimeLocal,
  toTimeInput,
} from '@/lib/format'
import type { ColumnMeta, FieldType } from '@/types'

// The test runner pins TZ=America/New_York (see src/test-setup.ts) so the
// UTC↔local conversions below are genuinely exercised, not no-ops under UTC.

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

describe('humanizeColumn', () => {
  it('splits camel/Pascal case and normalises Id → ID', () => {
    expect(humanizeColumn('DepartmentName')).toBe('Department Name')
    expect(humanizeColumn('UserId')).toBe('User ID')
    expect(humanizeColumn('first_name')).toBe('first name')
    expect(humanizeColumn('order-code')).toBe('order code')
  })
})

describe('fieldLabel', () => {
  it('drops a trailing ID only for foreign keys', () => {
    expect(fieldLabel(col('DepartmentID', 'integer'))).toBe('Department ID')
    expect(
      fieldLabel(
        col('ManagerID', 'integer', {
          foreign_key: { schema: 'dbo', table: 'Employee', column: 'EmployeeID' },
        }),
      ),
    ).toBe('Manager')
  })
})

describe('isNumericColumn', () => {
  it('is true for the numeric field types only', () => {
    for (const t of ['integer', 'number', 'decimal'] as FieldType[]) {
      expect(isNumericColumn(col('X', t))).toBe(true)
    }
    for (const t of ['text', 'boolean', 'date', 'datetime', 'time'] as FieldType[]) {
      expect(isNumericColumn(col('X', t))).toBe(false)
    }
  })
})

describe('formatValue — empties & booleans', () => {
  it('returns null for null/undefined/empty so callers can render a placeholder', () => {
    const c = col('Name', 'text')
    expect(formatValue(null, c)).toBeNull()
    expect(formatValue(undefined, c)).toBeNull()
    expect(formatValue('', c)).toBeNull()
  })

  it('renders booleans as Yes/No', () => {
    const c = col('Active', 'boolean')
    expect(formatValue(true, c)).toBe('Yes')
    expect(formatValue(false, c)).toBe('No')
  })

  it('NULL_DISPLAY is a single em dash', () => {
    expect(NULL_DISPLAY).toBe('—')
  })
})

describe('formatValue — numbers', () => {
  it('groups integers (locale formatter)', () => {
    const c = col('Qty', 'integer')
    const expected = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(1234567)
    expect(formatValue(1234567, c)).toBe(expected)
  })

  it('honours decimal scale (fixed places)', () => {
    const c = col('Price', 'decimal', { scale: 2 })
    const expected = new Intl.NumberFormat(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(1234.5)
    // decimal arrives as a string from the API
    expect(formatValue('1234.5', c)).toBe(expected)
  })

  it('passes a non-numeric value through unchanged', () => {
    expect(formatValue('N/A', col('Qty', 'integer'))).toBe('N/A')
  })
})

describe('formatValue — date (timezone-safe, no day shift)', () => {
  it('renders the calendar date without shifting under a non-UTC zone', () => {
    const c = col('StartDate', 'date')
    // Built from parts in production → no tz shift. A naive `new Date("2024-01-01")`
    // would be UTC midnight = Dec 31 in America/New_York; assert we did NOT do that.
    const partsExpected = new Date(2024, 0, 1).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })
    const naiveBuggy = new Date('2024-01-01').toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })
    expect(formatValue('2024-01-01', c)).toBe(partsExpected)
    expect(formatValue('2024-01-01', c)).not.toBe(naiveBuggy) // proves tz-safety
  })

  it('returns the raw string for an unparseable date', () => {
    expect(formatValue('not-a-date', col('StartDate', 'date'))).toBe('not-a-date')
  })
})

describe('formatValue — datetime (stored UTC, shown local)', () => {
  it('appends Z and converts naive UTC to local time', () => {
    const c = col('CreatedAt', 'datetime')
    const expected = new Date('2024-01-01T10:00:00Z').toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
    const naiveLocal = new Date('2024-01-01T10:00:00').toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
    expect(formatValue('2024-01-01T10:00:00', c)).toBe(expected)
    // Under NY the converted time (05:00) differs from a naive local read (10:00).
    expect(formatValue('2024-01-01T10:00:00', c)).not.toBe(naiveLocal)
  })

  it('does not double-shift a value that already carries a zone', () => {
    const c = col('CreatedAt', 'datetime')
    const expected = new Date('2024-01-01T10:00:00Z').toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
    expect(formatValue('2024-01-01T10:00:00Z', c)).toBe(expected)
  })
})

describe('formatValue — time', () => {
  it('shows HH:MM:SS, trimming any fractional/zone suffix', () => {
    expect(formatValue('13:45:30', col('At', 'time'))).toBe('13:45:30')
    expect(formatValue('13:45:30.1234567', col('At', 'time'))).toBe('13:45:30')
  })
})

describe('input coercion', () => {
  it('toDateInput / toTimeInput slice to the input shape and tolerate null', () => {
    expect(toDateInput('2024-03-04T10:00:00')).toBe('2024-03-04')
    expect(toDateInput(null)).toBe('')
    expect(toTimeInput('13:45:30.99')).toBe('13:45:30')
    expect(toTimeInput('')).toBe('')
  })

  it('datetime-local round-trips through UTC unchanged', () => {
    // local(stored UTC) → input string → back to the same naive UTC instant
    const stored = '2024-01-01T10:00:00'
    expect(localInputToUtc(toDateTimeLocal(stored))).toBe(stored)
  })
})

describe('shortestFloatRepr', () => {
  it('strips float32 (REAL) read-back noise back to the intended value', () => {
    // What a REAL column round-trips as: the float32 of 33.33 widened to float64.
    expect(shortestFloatRepr(Math.fround(33.33))).toBe(33.33)
    expect(shortestFloatRepr(Math.fround(12.34))).toBe(12.34)
    expect(shortestFloatRepr(Math.fround(99.99))).toBe(99.99)
    expect(shortestFloatRepr(Math.fround(0.1))).toBe(0.1)
    // the exact value observed in manual testing
    expect(shortestFloatRepr(33.33000183105469)).toBe(33.33)
  })

  it('leaves genuine float64 (FLOAT) values untouched — full precision kept', () => {
    expect(shortestFloatRepr(3.141592653589793)).toBe(3.141592653589793)
    expect(shortestFloatRepr(0.1 + 0.2)).toBe(0.30000000000000004)
  })

  it('passes integers, zero, negatives and non-finite values through', () => {
    expect(shortestFloatRepr(42)).toBe(42)
    expect(shortestFloatRepr(0)).toBe(0)
    expect(shortestFloatRepr(Math.fround(-12.34))).toBe(-12.34)
    expect(shortestFloatRepr(Number.NaN)).toBeNaN()
    expect(shortestFloatRepr(Infinity)).toBe(Infinity)
  })
})
