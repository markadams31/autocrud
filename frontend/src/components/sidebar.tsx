/**
 * sidebar.tsx — Schema and table navigation.
 *
 * Schemas come from GET /meta; each schema's tables (and the user's
 * permissions on them) come from GET /meta/{schema}. Only tables the signed-in
 * user can read appear here — the backend already filters the list to the
 * caller's grants, so the sidebar is an accurate map of what they can touch.
 *
 * Micro-interactions: the active item's background is a single shared element
 * (Motion layoutId) that glides between tables as the selection changes, and
 * each schema group expands/collapses with a smooth height transition.
 */

import { useState } from 'react'
import { AnimatePresence, LayoutGroup, motion } from 'motion/react'
import { ChevronRightIcon, DatabaseZapIcon, TableIcon } from 'lucide-react'

import { Skeleton } from '@/components/ui/skeleton'
import { SettingsMenu } from '@/components/settings-menu'
import { ThemeToggle } from '@/components/theme-toggle'
import { useMe, useSchemas, useTables } from '@/hooks/queries'
import { easeOutExpo, indicatorSpring } from '@/lib/animations'
import { cn } from '@/lib/utils'
import type { Selection } from '@/hooks/use-url-state'

interface SidebarProps {
  selection: Selection
  onSelect: (selection: Selection) => void
  /** Warm a table's cache on hover/focus so opening it is instant. */
  onPrefetch?: (schema: string, table: string) => void
}

/** Up-to-two-letter initials from a display name or email, for the avatar. */
function initialsOf(name: string): string {
  const parts = name.replace(/@.*$/, '').split(/[.\s_-]+/).filter(Boolean)
  return ((parts[0]?.[0] ?? '?') + (parts[1]?.[0] ?? '')).toUpperCase()
}

/** Footer chip showing the EasyAuth-authenticated user, when one is present. */
function UserBadge() {
  const { data: me } = useMe()
  if (!me?.name) return null
  return (
    <div className="flex items-center gap-2.5 border-t px-3 py-2.5">
      <div
        className="flex size-7 shrink-0 items-center justify-center rounded-full bg-sidebar-accent text-[11px] font-semibold text-sidebar-accent-foreground ring-1 ring-sidebar-border"
        aria-hidden
      >
        {initialsOf(me.name)}
      </div>
      <div className="min-w-0 leading-tight">
        <p className="truncate text-sm font-medium" title={me.name}>
          {me.name}
        </p>
        <p className="text-xs text-sidebar-foreground/50">Signed in</p>
      </div>
    </div>
  )
}

function SchemaGroup({
  schema,
  selection,
  onSelect,
  onPrefetch,
}: {
  schema: string
  selection: Selection
  onSelect: (s: Selection) => void
  onPrefetch?: (schema: string, table: string) => void
}) {
  const [open, setOpen] = useState(true)
  const { data: tables, isLoading } = useTables(schema)

  return (
    <div className="space-y-0.5">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-semibold tracking-wide text-sidebar-foreground/70 uppercase transition-colors outline-none hover:bg-sidebar-accent/40 hover:text-sidebar-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50"
      >
        <ChevronRightIcon
          className={cn('size-3.5 transition-transform duration-200', open && 'rotate-90')}
        />
        <span className="truncate">{schema}</span>
        {tables && (
          <span className="ml-auto text-[10px] font-normal text-sidebar-foreground/40 tabular-nums">
            {tables.length}
          </span>
        )}
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: easeOutExpo }}
            className="overflow-hidden"
          >
            <div className="space-y-0.5 pt-0.5 pl-2.5">
              {isLoading ? (
                Array.from({ length: 4 }).map((_, i) => (
                  <div key={i} className="flex items-center gap-2 px-2 py-1.5">
                    <Skeleton className="size-3.5 rounded" />
                    <Skeleton className="h-3.5 flex-1" style={{ maxWidth: `${60 + i * 8}%` }} />
                  </div>
                ))
              ) : tables && tables.length > 0 ? (
                tables.map((t) => {
                  const active = selection.schema === schema && selection.table === t.name
                  return (
                    <button
                      key={t.name}
                      type="button"
                      onClick={() => onSelect({ schema, table: t.name })}
                      onMouseEnter={() => onPrefetch?.(schema, t.name)}
                      onFocus={() => onPrefetch?.(schema, t.name)}
                      aria-current={active ? 'page' : undefined}
                      className={cn(
                        'relative flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-[color,background-color,transform] duration-150 outline-none active:scale-[0.98] focus-visible:ring-[3px] focus-visible:ring-ring/50',
                        active
                          ? 'font-medium text-sidebar-accent-foreground'
                          : 'text-sidebar-foreground/80 hover:translate-x-0.5 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground',
                      )}
                    >
                      {active && (
                        <motion.span
                          layoutId="sidebar-active-table"
                          className="absolute inset-0 rounded-md bg-sidebar-accent"
                          transition={indicatorSpring}
                        />
                      )}
                      <TableIcon
                        className={cn(
                          'relative size-3.5 shrink-0',
                          active ? 'text-sidebar-accent-foreground' : 'text-sidebar-foreground/40',
                        )}
                      />
                      <span className="relative truncate">{t.name}</span>
                    </button>
                  )
                })
              ) : (
                <p className="px-2 py-1.5 text-xs text-sidebar-foreground/40">No tables</p>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

export function Sidebar({ selection, onSelect, onPrefetch }: SidebarProps) {
  const { data, isLoading, isError } = useSchemas()
  const schemas = data?.schemas
  const database = data?.database

  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground">
      {/* Brand — labelled with the connected database */}
      <div className="flex items-center gap-2.5 border-b px-4 py-3.5">
        <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-600 text-white shadow-sm ring-1 ring-black/5">
          <DatabaseZapIcon className="size-4.5" />
        </div>
        <div className="min-w-0 leading-tight">
          {database ? (
            <p className="font-heading truncate text-sm font-semibold" title={database}>
              {database}
            </p>
          ) : isLoading ? (
            <Skeleton className="h-4 w-28" />
          ) : (
            <p className="font-heading text-sm font-semibold text-sidebar-foreground/50">Not connected</p>
          )}
          <p className="text-xs text-sidebar-foreground/50">Schema explorer</p>
        </div>
      </div>

      {/* Schemas */}
      <nav className="flex-1 space-y-2 overflow-y-auto p-2">
        {isLoading ? (
          Array.from({ length: 2 }).map((_, i) => (
            <div key={i} className="space-y-1">
              <Skeleton className="mx-2 my-1.5 h-3 w-20" />
              {Array.from({ length: 3 }).map((_, j) => (
                <Skeleton key={j} className="mx-2 h-7 rounded-md" />
              ))}
            </div>
          ))
        ) : isError ? (
          <p className="px-3 py-2 text-xs text-sidebar-foreground/50">
            Couldn’t load schemas. Is the API running?
          </p>
        ) : schemas && schemas.length > 0 ? (
          <LayoutGroup>
            {schemas.map((schema) => (
              <SchemaGroup
                key={schema}
                schema={schema}
                selection={selection}
                onSelect={onSelect}
                onPrefetch={onPrefetch}
              />
            ))}
          </LayoutGroup>
        ) : (
          <p className="px-3 py-2 text-xs text-sidebar-foreground/50">No schemas found.</p>
        )}
      </nav>

      {/* Footer — signed-in user + settings/theme controls */}
      <div>
        <UserBadge />
        <div className="flex items-center gap-0.5 border-t px-2.5 py-2">
          <SettingsMenu />
          <ThemeToggle />
        </div>
      </div>
    </aside>
  )
}
