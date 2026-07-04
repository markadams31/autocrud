/**
 * foreign-key-cell.tsx — A foreign-key value, with a hover preview.
 *
 * In the grid an FK column shows its resolved display label as a small chip;
 * hovering it opens a card that fetches and previews the referenced record (its
 * headline plus a handful of key fields). The fetch is gated on the card opening
 * and cached, so hovering the same reference again is instant and an unopened
 * card costs nothing. Used by the grid's `Cell` renderer.
 */

import { useState } from 'react'

import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from '@/components/ui/hover-card'
import { Skeleton } from '@/components/ui/skeleton'
import { useRecord, useTableMeta } from '@/hooks/queries'
import { fieldLabel, formatValue, NULL_DISPLAY } from '@/lib/format'
import type { ColumnMeta } from '@/types'

/** An FK chip that previews the referenced record in a hover-card. */
export function ForeignKeyCell({
  fk,
  value,
  label,
}: {
  fk: NonNullable<ColumnMeta['foreign_key']>
  value: unknown
  label: string
}) {
  const [open, setOpen] = useState(false)
  return (
    <HoverCard open={open} onOpenChange={setOpen}>
      <HoverCardTrigger
        render={
          <span className="inline-block max-w-full cursor-default truncate rounded-md bg-accent px-2 py-0.5 align-middle text-xs font-medium text-accent-foreground ring-1 ring-inset ring-accent-foreground/15" />
        }
      >
        {label}
      </HoverCardTrigger>
      <HoverCardContent>
        <ForeignKeyPreview fk={fk} value={String(value)} open={open} />
      </HoverCardContent>
    </HoverCard>
  )
}

/** The body of the FK hover-card: the referenced record's headline + key fields. */
function ForeignKeyPreview({
  fk,
  value,
  open,
}: {
  fk: NonNullable<ColumnMeta['foreign_key']>
  value: string
  open: boolean
}) {
  // Only fetch once the card opens (and reuse the cache on repeat hovers).
  const metaQuery = useTableMeta(open ? fk.schema : null, open ? fk.table : null)
  const recordQuery = useRecord(fk.schema, fk.table, value, open)
  const meta = metaQuery.data
  const record = recordQuery.data
  const loading = recordQuery.isLoading || metaQuery.isLoading

  const title =
    meta?.display_column && record?.[meta.display_column] != null
      ? String(record[meta.display_column])
      : `#${value}`

  // A shallow preview: skip the headline column, audit columns, nested FKs
  // (no labels here), and nulls; show the first handful.
  const fields = (meta?.columns ?? [])
    .filter(
      (c) =>
        c.name !== meta?.display_column &&
        !c.is_audit &&
        !c.foreign_key &&
        record?.[c.name] != null,
    )
    .slice(0, 6)

  return (
    <div className="space-y-2.5">
      <div className="space-y-0.5">
        <p className="text-[10px] font-medium tracking-wide text-muted-foreground uppercase">
          {fk.schema}.{fk.table}
        </p>
        {loading ? (
          <Skeleton className="h-4 w-32" />
        ) : (
          <p className="font-heading text-sm font-semibold break-words">{title}</p>
        )}
      </div>

      {recordQuery.isError ? (
        <p className="text-xs text-muted-foreground">
          Couldn’t load this record — you may not have access to {fk.table}.
        </p>
      ) : loading ? (
        <div className="space-y-1.5">
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-4/5" />
          <Skeleton className="h-3 w-3/5" />
        </div>
      ) : (
        <dl className="space-y-1.5">
          {fields.map((c) => (
            <div key={c.name} className="flex items-baseline justify-between gap-3 text-xs">
              <dt className="shrink-0 text-muted-foreground">{fieldLabel(c)}</dt>
              <dd className="min-w-0 truncate text-right font-medium tabular-nums">
                {formatValue(record![c.name], c) ?? NULL_DISPLAY}
              </dd>
            </div>
          ))}
          {fields.length === 0 && (
            <p className="text-xs text-muted-foreground">No additional details.</p>
          )}
        </dl>
      )}
    </div>
  )
}
