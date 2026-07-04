/**
 * delete-dialog.tsx — Confirmation before a destructive delete.
 *
 * A controlled dialog (no trigger) so the parent decides which row is being
 * deleted. The confirm button reuses the morphing save button, switched to the
 * destructive variant, so deletion has the same considered loading feedback as
 * every other write.
 */

import { TriangleAlertIcon } from 'lucide-react'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { SaveButton } from '@/components/save-button'

interface DeleteDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Human-readable label of the single row being deleted (single-row mode). */
  label?: string
  /** Number of rows in a bulk delete. When set (>1), bulk copy is shown. */
  count?: number
  loading: boolean
  onConfirm: () => void
}

export function DeleteDialog({
  open,
  onOpenChange,
  label,
  count,
  loading,
  onConfirm,
}: DeleteDialogProps) {
  // `count != null` selects bulk mode (even for a single ticked row); otherwise
  // it's the single-row dialog driven by `label`.
  const bulk = count != null
  const noun = count === 1 ? 'record' : 'records'
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={false}>
        <DialogHeader>
          <div className="flex size-9 items-center justify-center rounded-full bg-destructive/10 text-destructive">
            <TriangleAlertIcon className="size-5" />
          </div>
          <DialogTitle>
            {bulk ? `Delete ${count} ${noun}?` : 'Delete this record?'}
          </DialogTitle>
          <DialogDescription>
            {bulk ? (
              <>
                You’re about to permanently delete{' '}
                <span className="font-medium text-foreground">
                  {count} {noun}
                </span>
                . This action cannot be undone.
              </>
            ) : (
              <>
                You’re about to permanently delete{' '}
                <span className="font-medium text-foreground">{label}</span>. This action
                cannot be undone.
              </>
            )}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={loading}
          >
            Cancel
          </Button>
          <SaveButton
            type="button"
            variant="destructive"
            loading={loading}
            onClick={onConfirm}
          >
            Delete
          </SaveButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
