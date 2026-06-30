/**
 * validation-toast.ts — The "fix the highlighted fields" toast for a form.
 *
 * Both the record form and the bulk-edit form surface server-side field errors
 * the same way: a single toast, bound to a stable id so repeated failed submits
 * replace rather than stack, dismissed the moment the form becomes valid again
 * (a field is edited, or the save succeeds). Each form passes its own id so two
 * forms never clobber each other's toast.
 */

import { toast } from 'sonner'

export function validationToast(id: string) {
  return {
    /** Show (or replace) the toast after a submit failed field validation. */
    show: () => {
      toast.error('Please fix the highlighted fields.', { id })
    },
    /** Retire it — on a field edit, or a successful save. */
    dismiss: () => {
      toast.dismiss(id)
    },
  }
}
