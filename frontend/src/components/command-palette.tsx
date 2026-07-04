/**
 * command-palette.tsx — ⌘K / Ctrl-K quick switcher.
 *
 * A keyboard-first jump-to-table: substring search across every table the user
 * can see (reusing the same cached per-schema lists the sidebar loads), arrow
 * keys to move the highlight, Enter or click to navigate, Esc to close.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { useQueries } from '@tanstack/react-query'
import { CornerDownLeftIcon, SearchIcon, TableIcon } from 'lucide-react'

import { queryKeys, useSchemas } from '@/hooks/queries'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'

interface TableItem {
  schema: string
  table: string
}

interface CommandPaletteProps {
  open: boolean
  onClose: () => void
  onSelect: (schema: string, table: string) => void
}

export function CommandPalette({ open, onClose, onSelect }: CommandPaletteProps) {
  const { data: schemaList } = useSchemas()
  const schemas = useMemo(() => schemaList?.schemas ?? [], [schemaList])

  // Every (schema, table) the user can see. Same query keys as the sidebar's
  // useTables, so this reads from cache rather than refetching.
  const items = useQueries({
    queries: schemas.map((s) => ({
      queryKey: queryKeys.tables(s),
      queryFn: () => api.listTables(s).then((r) => r.tables),
    })),
    combine: (results): TableItem[] =>
      results.flatMap((r, i) => (r.data ?? []).map((t) => ({ schema: schemas[i], table: t.name }))),
  })

  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const listRef = useRef<HTMLUListElement>(null)

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return items
    return items.filter((it) => `${it.schema}.${it.table}`.toLowerCase().includes(q))
  }, [items, query])

  // Fresh search each time it opens.
  useEffect(() => {
    if (open) {
      setQuery('')
      setActive(0)
    }
  }, [open])

  // Keep the highlight in range as the result set shrinks, and scroll it in view.
  useEffect(() => {
    setActive((a) => Math.min(a, Math.max(0, filtered.length - 1)))
  }, [filtered.length])
  useEffect(() => {
    listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [active])

  function choose(item: TableItem | undefined) {
    if (!item) return
    onSelect(item.schema, item.table)
    onClose()
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((a) => Math.min(a + 1, filtered.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((a) => Math.max(a - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      choose(filtered[active])
    }
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop className="fixed inset-0 z-50 bg-foreground/20 transition-opacity duration-150 supports-[backdrop-filter]:backdrop-blur-[2px] data-starting-style:opacity-0 data-ending-style:opacity-0" />
        <DialogPrimitive.Popup
          aria-label="Search tables"
          className="fixed top-[18%] left-1/2 z-50 w-full max-w-lg -translate-x-1/2 overflow-hidden rounded-xl border bg-popover text-popover-foreground shadow-2xl ring-1 ring-border outline-none transition-all duration-150 data-starting-style:scale-95 data-starting-style:opacity-0 data-ending-style:scale-95 data-ending-style:opacity-0"
        >
          <div className="flex items-center gap-2 border-b px-3">
            <SearchIcon className="size-4 shrink-0 text-muted-foreground" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Jump to a table…"
              aria-label="Jump to a table"
              className="h-11 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
          </div>

          <ul ref={listRef} className="max-h-72 overflow-y-auto p-1.5">
            {filtered.length === 0 ? (
              <li className="px-3 py-6 text-center text-sm text-muted-foreground">No tables found.</li>
            ) : (
              filtered.map((item, i) => {
                const isActive = i === active
                return (
                  <li key={`${item.schema}.${item.table}`}>
                    <button
                      type="button"
                      data-active={isActive}
                      onMouseMove={() => setActive(i)}
                      onClick={() => choose(item)}
                      className={cn(
                        'flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm transition-colors',
                        isActive ? 'bg-accent text-accent-foreground' : 'text-foreground',
                      )}
                    >
                      <TableIcon className="size-4 shrink-0 text-muted-foreground" />
                      <span className="truncate">
                        <span className="text-muted-foreground">{item.schema}.</span>
                        <span className="font-medium">{item.table}</span>
                      </span>
                      {isActive && (
                        <CornerDownLeftIcon className="ml-auto size-3.5 shrink-0 text-muted-foreground" />
                      )}
                    </button>
                  </li>
                )
              })
            )}
          </ul>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
