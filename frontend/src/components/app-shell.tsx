/**
 * app-shell.tsx — Top-level layout: persistent sidebar + the selected table.
 *
 * The selected (schema, table) lives in the URL via useUrlState, so the view is
 * deep-linkable and survives a refresh. TableView is keyed by the selection so
 * switching tables remounts it with fresh query state. The shell also warms the
 * cache on sidebar hover (so tables open instantly) and hosts the ⌘K palette.
 */

import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ArrowLeftIcon } from 'lucide-react'

import { CommandPalette } from '@/components/command-palette'
import { Sidebar } from '@/components/sidebar'
import { TableView } from '@/components/table-view'
import { EmptyState } from '@/components/states'
import { queryKeys } from '@/hooks/queries'
import { useUrlState } from '@/hooks/use-url-state'
import { api } from '@/lib/api'
import type { QueryRequest } from '@/types'

const META_STALE_TIME = 5 * 60 * 1000

export function AppShell() {
  const url = useUrlState()
  const queryClient = useQueryClient()
  const [paletteOpen, setPaletteOpen] = useState(false)

  // ⌘K / Ctrl-K toggles the quick switcher.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen((o) => !o)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Hover-intent prefetch: warm a table's metadata + first page so clicking it
  // renders instantly from cache instead of flashing skeletons. The request
  // matches the default view selectTable lands on, so the key is a cache hit.
  function prefetchTable(schema: string, table: string) {
    queryClient.prefetchQuery({
      queryKey: queryKeys.tableMeta(schema, table),
      queryFn: () => api.describeTable(schema, table),
      staleTime: META_STALE_TIME,
    })
    const req: QueryRequest = {
      search: '',
      filters: {},
      sort: { column: '', direction: 'asc' },
      page: 1,
      page_size: url.pageSize,
    }
    queryClient.prefetchQuery({
      queryKey: queryKeys.rows(schema, table, req),
      queryFn: () => api.query(schema, table, req),
      staleTime: 30_000,
    })
  }

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background text-foreground">
      {/* Skip link — the first focusable element, so keyboard and screen-reader
          users can jump past the sidebar navigation straight to the content. */}
      <a
        href="#main-content"
        className="sr-only rounded-md bg-background px-4 py-2 text-sm font-medium shadow-lg ring-2 ring-ring focus:not-sr-only focus:absolute focus:top-4 focus:left-4 focus:z-50"
      >
        Skip to main content
      </a>
      <Sidebar
        selection={{ schema: url.schema, table: url.table }}
        onSelect={(s) => url.selectTable(s.schema!, s.table!)}
        onPrefetch={prefetchTable}
      />
      <main id="main-content" tabIndex={-1} className="min-w-0 flex-1 overflow-hidden bg-muted/20 outline-none">
        {url.schema && url.table ? (
          <TableView
            key={`${url.schema}.${url.table}`}
            schema={url.schema}
            table={url.table}
            url={url}
          />
        ) : (
          <div className="flex h-full items-center justify-center">
            <EmptyState
              icon={ArrowLeftIcon}
              title="Select a table"
              description="Choose a table from the sidebar to browse, search, and edit its records. Everything you see is generated live from the database schema. Press ⌘K to jump to any table."
            />
          </div>
        )}
      </main>
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        onSelect={(schema, table) => url.selectTable(schema, table)}
      />
    </div>
  )
}
