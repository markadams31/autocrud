/**
 * errors.ts — Turning a thrown error into something the UI can show.
 *
 * Mutations reject with an `ApiError` carrying the backend's machine `code`, a
 * safe human `message`, and optional per-field detail. These helpers are the few
 * shapes the UI actually needs — a display message, a "is this a constraint
 * block?" check, and form field errors — so every catch handler reads the same
 * way instead of re-deriving `err instanceof ApiError ? … : …` inline.
 */

import { ApiError } from '@/lib/api'

/** A safe, human message for any thrown error, falling back to `fallback`. */
export function messageFor(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

/**
 * True when the error is the API's generic constraint violation. The backend
 * never names tables/keys, so callers phrase their own context-specific copy
 * (e.g. "still referenced by other records") off this.
 */
export function isConstraintViolation(err: unknown): boolean {
  return err instanceof ApiError && err.code === 'CONSTRAINT_VIOLATION'
}

/**
 * True when the error is an optimistic-concurrency conflict — the row changed
 * (or was deleted) since it was read, so the write was refused. Callers refetch
 * to pick up the latest version and tell the user to reapply.
 */
export function isConflict(err: unknown): boolean {
  return err instanceof ApiError && err.code === 'CONFLICT'
}

/** Per-field validation messages from an API error, for highlighting a form. */
export function fieldErrors(err: unknown): Record<string, string> | undefined {
  return err instanceof ApiError ? err.fields : undefined
}
