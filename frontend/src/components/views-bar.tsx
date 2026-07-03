/**
 * views-bar.tsx — Named view pills for quick data-view switching.
 *
 * Shows an "All" pill (resets filters/sort/layout) plus one pill per saved
 * view. When the current state differs from "all defaults", a "Save view"
 * affordance appears so users can name and persist the active combination.
 * Saved views are stored in localStorage via useSavedViews (use-table-prefs).
 */

import { useRef, useState } from 'react'
import { PlusIcon, XIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import type { SavedView } from '@/hooks/use-table-prefs'

interface ViewsBarProps {
  views: SavedView[]
  activeViewId: string | null
  /** True when the current state is fully default (no sort, filters, or layout changes). */
  isAllActive: boolean
  /** True when the current state differs from "no filters, no sort, default layout". */
  canSave: boolean
  onSelect: (view: SavedView | null) => void
  onSave: (name: string) => void
  onDelete: (id: string) => void
}

export function ViewsBar({
  views,
  activeViewId,
  isAllActive,
  canSave,
  onSelect,
  onSave,
  onDelete,
}: ViewsBarProps) {
  const [saving, setSaving] = useState(false)
  const [name, setName] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  function handleSave() {
    const trimmed = name.trim()
    if (!trimmed) return
    onSave(trimmed)
    setName('')
    setSaving(false)
  }

  function openSaving() {
    setSaving(true)
    // Focus the input on the next tick once it renders.
    setTimeout(() => inputRef.current?.focus(), 0)
  }

  if (views.length === 0 && !canSave) return null

  return (
    <div className="flex flex-wrap items-center gap-1">
      {/* "All" pill — always shown when there are saved views */}
      {views.length > 0 && (
        <ViewPill
          label="All"
          active={isAllActive}
          onClick={() => onSelect(null)}
        />
      )}

      {views.map((view) => (
        <div key={view.id} className="group/view flex items-center">
          <ViewPill
            label={view.name}
            active={activeViewId === view.id}
            onClick={() => onSelect(view)}
          />
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              onDelete(view.id)
            }}
            className="ml-0.5 rounded p-0.5 text-muted-foreground opacity-0 transition-opacity hover:text-foreground group-hover/view:opacity-100 group-focus-within/view:opacity-100"
            aria-label={`Delete view "${view.name}"`}
          >
            <XIcon className="size-3" />
          </button>
        </div>
      ))}

      {saving ? (
        <div className="flex items-center gap-1">
          <Input
            ref={inputRef}
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleSave()
              if (e.key === 'Escape') {
                setSaving(false)
                setName('')
              }
            }}
            placeholder="View name…"
            className="h-6 w-32 px-2 text-xs"
          />
          <Button
            size="sm"
            className="h-6 px-2 text-xs"
            onClick={handleSave}
            disabled={!name.trim()}
          >
            Save
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-6 px-2 text-xs"
            onClick={() => {
              setSaving(false)
              setName('')
            }}
          >
            Cancel
          </Button>
        </div>
      ) : canSave ? (
        <button
          type="button"
          onClick={openSaving}
          className="flex items-center gap-1 rounded-md px-2 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <PlusIcon className="size-3" />
          Save view
        </button>
      ) : null}
    </div>
  )
}

function ViewPill({
  label,
  active,
  onClick,
}: {
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'rounded-md px-2.5 py-0.5 text-xs font-medium transition-colors',
        active
          ? 'bg-primary text-primary-foreground'
          : 'text-muted-foreground hover:bg-muted hover:text-foreground',
      )}
    >
      {label}
    </button>
  )
}
