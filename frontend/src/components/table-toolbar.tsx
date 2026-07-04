/**
 * table-toolbar.tsx — Search, grid controls slot, refresh, and the New action.
 */

import type { ReactNode } from 'react'
import { PlusIcon, SearchIcon, XIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

interface TableToolbarProps {
  search: string
  onSearchChange: (value: string) => void
  canInsert: boolean
  onNew: () => void
  /** Grid controls (columns, density, export) rendered between search and New. */
  actions?: ReactNode
}

export function TableToolbar({
  search,
  onSearchChange,
  canInsert,
  onNew,
  actions,
}: TableToolbarProps) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="relative w-full sm:w-auto sm:max-w-xs sm:flex-1">
        <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="Search all text columns…"
          className="px-8"
          aria-label="Search"
        />
        {search && (
          <button
            type="button"
            onClick={() => onSearchChange('')}
            aria-label="Clear search"
            className="absolute top-1/2 right-2 -translate-y-1/2 rounded text-muted-foreground transition-colors outline-none hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50"
          >
            <XIcon className="size-4" />
          </button>
        )}
      </div>

      <div className="flex items-center gap-2 sm:ml-auto">
        {actions}
        {canInsert && (
          <Button onClick={onNew}>
            <PlusIcon />
            New record
          </Button>
        )}
      </div>
    </div>
  )
}
