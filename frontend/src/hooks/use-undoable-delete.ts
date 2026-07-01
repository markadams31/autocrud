/**
 * use-undoable-delete.ts — Delete row(s) with a grace period to undo.
 *
 * The row(s) are removed from the cache immediately, so the grid reacts at once
 * (and plays the exit animation), and a Sonner toast offers "Undo" while counting
 * down the grace period second-by-second ("Deleting … in N seconds"). The real
 * DELETE is deferred until the countdown reaches zero; clicking Undo cancels it
 * and restores the cached rows — no server round-trip at all. If the server
 * delete ultimately fails, the rows are restored and an error toast is shown.
 *
 * `remove` deletes one row; `removeMany` deletes an explicit selection in one
 * atomic bulk-delete — same instant + undoable feel. Set-based "all matching"
 * deletes stay in the confirm dialog (we can't hold those rows to restore).
 *
 * This sits a level above the pure query hooks (it composes the cache with toast
 * and a timer), so it lives in its own feature hook rather than queries.ts.
 */

import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { invalidateTableWrites, queryKeys } from '@/hooks/queries'
import { api, encodePk } from '@/lib/api'
import { isConflict, isConstraintViolation, messageFor } from '@/lib/errors'
import { trackEvent } from '@/lib/telemetry'
import type { QueryResponse, Row } from '@/types'

/**
 * How long the rows stay "deleted-but-undoable" before the server call fires.
 * The toast counts this window down second-by-second, so the user can see exactly
 * how long they have left to undo.
 */
const UNDO_WINDOW_SECONDS = 5

/**
 * A human delete-failure message. The API keeps CONSTRAINT_VIOLATION generic
 * (it never names tables/keys), but a failed delete is almost always blocked by
 * a foreign key — another row still points at this one — so we say that.
 */
function deleteErrorMessage(err: unknown, what: string): string {
  if (isConflict(err)) {
    return `Didn't delete ${what} — it was changed by someone else since you loaded it. The latest version has been restored.`
  }
  if (isConstraintViolation(err)) {
    // The backend now names which table still references this row; surface that
    // precise reason, falling back to a generic sentence if it didn't.
    return messageFor(err, `Can't delete ${what} because other records still reference it.`)
  }
  return messageFor(err, `Could not delete ${what}.`)
}

function bulkDeleteErrorMessage(err: unknown): string {
  if (isConstraintViolation(err)) {
    return 'Some of these records are still referenced by other records, so none were deleted.'
  }
  return messageFor(err, 'Could not delete the selected records.')
}

interface UndoableDelete {
  /** Which cached rows to optimistically remove. */
  matches: (row: Row) => boolean
  /** Toast copy shown while the delete is pending (and undoable). */
  pendingMessage: string
  /** Performs the real delete; resolves to the success-toast message. */
  commit: () => Promise<string>
  errorMessage: (err: unknown) => string
}

/** Optimistically remove rows, show an Undo toast, and defer the real delete. */
function beginUndoableDelete(
  qc: QueryClient,
  schema: string,
  table: string,
  opts: UndoableDelete,
) {
  const rowsKey = queryKeys.rowsAll(schema, table)
  void qc.cancelQueries({ queryKey: rowsKey })
  const previous = qc.getQueriesData<QueryResponse>({ queryKey: rowsKey })
  qc.setQueriesData<QueryResponse>({ queryKey: rowsKey }, (old) => {
    if (!old) return old
    const data = old.data.filter((r) => !opts.matches(r))
    return { ...old, data, total: Math.max(0, old.total - (old.data.length - data.length)) }
  })
  const restore = () => previous.forEach(([key, data]) => qc.setQueryData(key, data))

  // The toast title carries a live countdown of the undo window ("…in N seconds")
  // while the delete is only pending (it hasn't hit the server and could fail),
  // resolving in place to "Deleted." on success or the error if it's rejected.
  let undone = false
  let committing = false
  let remaining = UNDO_WINDOW_SECONDS
  let interval = 0
  let toastId: string | number = ''

  const title = (secs: number) =>
    `${opts.pendingMessage} in ${secs} second${secs === 1 ? '' : 's'}`

  const undo = () => {
    undone = true
    window.clearInterval(interval)
    restore()
    toast.dismiss(toastId) // an Infinity-duration toast won't close itself
    // `raced` = the undo landed *after* the real delete was already dispatched
    // (the toast stays clickable during the in-flight commit), so the restore is
    // only local while the server delete still commits. Telemetry surfaces how
    // often that race actually bites.
    trackEvent('row_delete_undo', { schema, table, raced: committing })
  }

  // Re-supplied on every toast update so the Undo button survives the countdown.
  const action = { label: 'Undo', onClick: undo }

  toastId = toast(title(remaining), { duration: Infinity, action })

  const commit = async () => {
    committing = true
    try {
      const successMessage = await opts.commit()
      invalidateTableWrites(qc, schema, table) // refresh rows + FK dropdowns
      toast.success(successMessage, { id: toastId, duration: 2500 })
      trackEvent('row_delete_committed', { schema, table })
    } catch (err) {
      restore()
      // Reconcile with the server after a failed delete: the row is back, but a
      // concurrency conflict means its rowversion moved on — refetch so the
      // restored row carries the current token and a retry won't falsely conflict.
      invalidateTableWrites(qc, schema, table)
      // A finite duration is required here: updating a toast by id keeps the
      // original options, so without this the error would inherit
      // duration:Infinity and never fade (and couldn't be dismissed).
      toast.error(opts.errorMessage(err), { id: toastId, duration: 6000 })
      // The optimistic removal was just rolled back — record it (and the coarse
      // reason) so cache-reconciliation failures are visible in telemetry.
      const code = isConflict(err) ? 'conflict' : isConstraintViolation(err) ? 'constraint' : 'other'
      trackEvent('row_delete_failed', { schema, table, code })
    }
  }

  // One timer drives both the countdown and the commit: each tick decrements the
  // displayed seconds, and the tick that reaches zero fires the real delete.
  interval = window.setInterval(() => {
    remaining -= 1
    if (remaining > 0) {
      toast(title(remaining), { id: toastId, duration: Infinity, action })
    } else {
      window.clearInterval(interval)
      if (!undone) void commit()
    }
  }, 1000)
}

export function useUndoableDelete(
  schema: string,
  table: string,
  primaryKey: string[],
  /** rowversion column name (TableMeta.concurrency_token), if the table has one. */
  tokenColumn?: string | null,
) {
  const queryClient = useQueryClient()

  /** Delete one row, naming it in the toast. */
  function remove(row: Row, { label }: { label?: string } = {}) {
    const what = label ?? 'this record'
    const pk = encodePk(row, primaryKey)
    // Send the row's rowversion as If-Match so a stale view can't delete a newer
    // revision (optimistic concurrency). Omitted when the table has no rowversion.
    const ifMatch =
      tokenColumn && row[tokenColumn] != null ? String(row[tokenColumn]) : undefined
    beginUndoableDelete(queryClient, schema, table, {
      matches: (r) => encodePk(r, primaryKey) === pk,
      pendingMessage: `Deleting ${what}`,
      commit: async () => {
        await api.deleteRow(schema, table, pk, ifMatch)
        return `Deleted ${what}.`
      },
      errorMessage: (err) => deleteErrorMessage(err, what),
    })
  }

  /** Delete an explicit selection of rows in one atomic, undoable bulk-delete. */
  function removeMany(rows: Row[]) {
    const n = rows.length
    if (n === 0) return
    const noun = n === 1 ? 'record' : 'records'
    const keys = new Set(rows.map((r) => encodePk(r, primaryKey)))
    const ids = rows.map((r) => primaryKey.map((k) => r[k]))
    beginUndoableDelete(queryClient, schema, table, {
      matches: (r) => keys.has(encodePk(r, primaryKey)),
      pendingMessage: `Deleting ${n} ${noun}`,
      commit: async () => {
        const res = await api.bulkDelete(schema, table, { ids })
        return `Deleted ${res.deleted} ${res.deleted === 1 ? 'record' : 'records'}.`
      },
      errorMessage: bulkDeleteErrorMessage,
    })
  }

  return { remove, removeMany }
}
