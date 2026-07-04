import { describe, expect, it } from 'vitest'

import { ApiError } from '@/lib/api'
import { fieldErrors, isConflict, isConstraintViolation, messageFor } from '@/lib/errors'

const apiError = (code: string, extra: Record<string, unknown> = {}) =>
  new ApiError(400, { code: code as never, message: `msg-${code}`, ...extra })

describe('messageFor', () => {
  it('returns an ApiError message, or the fallback for anything else', () => {
    expect(messageFor(apiError('CONSTRAINT_VIOLATION'), 'fallback')).toBe('msg-CONSTRAINT_VIOLATION')
    expect(messageFor(new Error('raw'), 'fallback')).toBe('fallback')
    expect(messageFor('a string', 'fallback')).toBe('fallback')
    expect(messageFor(undefined, 'fallback')).toBe('fallback')
  })
})

describe('isConstraintViolation', () => {
  it('is true only for an ApiError with the constraint code', () => {
    expect(isConstraintViolation(apiError('CONSTRAINT_VIOLATION'))).toBe(true)
    expect(isConstraintViolation(apiError('NOT_FOUND'))).toBe(false)
    expect(isConstraintViolation(new Error('x'))).toBe(false)
    expect(isConstraintViolation(null)).toBe(false)
  })
})

describe('isConflict', () => {
  it('is true only for an ApiError with the conflict code', () => {
    expect(isConflict(apiError('CONFLICT'))).toBe(true)
    expect(isConflict(apiError('CONSTRAINT_VIOLATION'))).toBe(false)
    expect(isConflict(new Error('x'))).toBe(false)
  })
})

describe('fieldErrors', () => {
  it('returns per-field errors from an ApiError, else undefined', () => {
    const fields = { Email: 'Already taken.' }
    expect(fieldErrors(apiError('VALIDATION_ERROR', { fields }))).toEqual(fields)
    expect(fieldErrors(apiError('VALIDATION_ERROR'))).toBeUndefined()
    expect(fieldErrors(new Error('x'))).toBeUndefined()
  })
})
