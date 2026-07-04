/**
 * mock-api.contract.test.ts — Guards the e2e mock against drifting from the real
 * API contract (frontend/src/types.ts). The e2e suite is only as trustworthy as
 * its stand-in backend: if mock-api returns a shape the real API doesn't, the
 * e2e tests pass while the app would break in production. This asserts the
 * mock's metadata fixtures structurally match ColumnMeta / TableMeta /
 * TableSummary. (Run by Vitest — e2e/ isn't type-checked by the build.)
 */
import { describe, expect, it } from 'vitest'

import { tableMeta, tablesList } from './mock-api'
import type { ColumnMeta } from '../src/types'

const FIELD_TYPES = new Set([
  'text', 'integer', 'number', 'decimal', 'boolean', 'date', 'datetime', 'time',
])

// The exact, complete key set ColumnMeta requires — extra or missing keys fail.
const COLUMN_KEYS: (keyof ColumnMeta)[] = [
  'name', 'field_type', 'nullable', 'required', 'editable',
  'is_primary_key', 'is_audit', 'max_length', 'precision', 'scale', 'foreign_key',
]

describe('mock-api metadata conforms to the API contract', () => {
  it('every table meta has the TableMeta shape (incl. concurrency_token)', () => {
    for (const [name, meta] of Object.entries(tableMeta)) {
      expect(typeof meta.schema, name).toBe('string')
      expect(typeof meta.name, name).toBe('string')
      expect(Array.isArray(meta.primary_key), name).toBe(true)
      // display_column and concurrency_token are nullable but must be present.
      expect('display_column' in meta, name).toBe(true)
      expect('concurrency_token' in meta, name).toBe(true)
      expect(Array.isArray(meta.columns), name).toBe(true)
    }
  })

  it('every column has exactly the ColumnMeta keys and a valid field_type', () => {
    for (const meta of Object.values(tableMeta)) {
      for (const c of meta.columns) {
        expect(Object.keys(c).sort()).toEqual([...COLUMN_KEYS].sort())
        expect(FIELD_TYPES.has(c.field_type), `${c.name}:${c.field_type}`).toBe(true)
        expect(typeof c.nullable).toBe('boolean')
        expect(typeof c.required).toBe('boolean')
        expect(typeof c.editable).toBe('boolean')
        // foreign_key is either null or a {schema,table,column} triple.
        if (c.foreign_key !== null) {
          expect(Object.keys(c.foreign_key).sort()).toEqual(['column', 'schema', 'table'])
        }
      }
    }
  })

  it('every sidebar entry has the TableSummary shape', () => {
    for (const t of tablesList) {
      expect(typeof t.name).toBe('string')
      expect('display_column' in t).toBe(true)
      expect(Array.isArray(t.primary_key)).toBe(true)
      expect(t.permissions).toEqual({
        insert: expect.any(Boolean),
        update: expect.any(Boolean),
        delete: expect.any(Boolean),
      })
    }
  })
})
