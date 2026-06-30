/**
 * bulk-action-bar.tsx — Contextual toolbar shown while rows are selected.
 *
 * Appears above the grid when one or more rows are selected and offers the
 * actions that apply to a selection — edit (one change → many rows) and delete,
 * each shown only if the user is allowed it. It also carries the Gmail-style
 * "select all matching" affordance: once every row loaded on the page is ticked
 * and more rows match the current query than are loaded, the user can extend the
 * selection to every matching row — which edit and delete then perform set-based
 * on the server (see TableView / crud.py bulk-update & bulk-delete).
 */

import { motion } from 'motion/react'
import { PencilIcon, XIcon } from 'lucide-react'

import { AnimatedTrash } from '@/components/animated-trash'
import { Button } from '@/components/ui/button'
import { easeOutExpo } from '@/lib/animations'

interface BulkActionBarProps {
  /** Effective number of selected rows (the whole matching set when allMatching). */
  count: number
  /** True when the selection has been extended to every matching row. */
  allMatching: boolean
  /** Total rows matching the current query — the size of an "all matching" selection. */
  total: number
  /** Show the "select all matching" affordance (all loaded ticked, more match). */
  canSelectAllMatching: boolean
  onSelectAllMatching: () => void
  onClear: () => void
  /** Shown only when the user can update the table. */
  onEdit?: () => void
  /** Shown only when the user can delete from the table. */
  onDelete?: () => void
}

export function BulkActionBar({
  count,
  allMatching,
  total,
  canSelectAllMatching,
  onSelectAllMatching,
  onClear,
  onEdit,
  onDelete,
}: BulkActionBarProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -6 }}
      transition={{ duration: 0.18, ease: easeOutExpo }}
      className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border bg-accent/40 px-3 py-2 text-sm shadow-sm"
      role="region"
      aria-label="Bulk actions"
    >
      <span className="font-medium tabular-nums">
        {count} selected
      </span>

      {allMatching ? (
        <span className="text-muted-foreground">
          All {total} matching rows are selected.
        </span>
      ) : canSelectAllMatching ? (
        <button
          type="button"
          onClick={onSelectAllMatching}
          className="rounded-sm font-medium text-primary underline-offset-2 outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50"
        >
          Select all {total} matching
        </button>
      ) : null}

      <div className="ml-auto flex items-center gap-2">
        {onEdit && (
          <Button variant="outline" size="sm" onClick={onEdit}>
            <PencilIcon />
            Edit
          </Button>
        )}
        {onDelete && (
          <Button variant="destructive" size="sm" onClick={onDelete}>
            <AnimatedTrash />
            Delete
          </Button>
        )}
        <Button variant="ghost" size="sm" onClick={onClear}>
          <XIcon />
          Clear
        </Button>
      </div>
    </motion.div>
  )
}
