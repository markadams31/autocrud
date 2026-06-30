/**
 * grid-controls.tsx — Column visibility and row-density menus for the grid.
 * Both are backed by persisted preferences (see use-table-prefs).
 */

import { Columns3Icon, Rows3Icon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import type { Density } from '@/hooks/use-table-prefs'
import { fieldLabel } from '@/lib/format'
import type { ColumnMeta } from '@/types'

export function ColumnsMenu({
  columns,
  hidden,
  onToggle,
  onReset,
  onHideManaged,
}: {
  columns: ColumnMeta[]
  hidden: Set<string>
  onToggle: (name: string) => void
  onReset: () => void
  /** Hide all database-managed (non-editable) columns in one click. */
  onHideManaged: () => void
}) {
  const visibleCount = columns.length - hidden.size
  // Offer the shortcut only while a managed column is actually still showing.
  const hasManagedVisible = columns.some((c) => !c.editable && !hidden.has(c.name))

  return (
    <DropdownMenu>
      <DropdownMenuTrigger render={<Button variant="outline" size="sm" />}>
        <Columns3Icon />
        Columns
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="max-h-80 w-52 overflow-y-auto">
        <DropdownMenuGroup>
          <DropdownMenuLabel>Toggle columns</DropdownMenuLabel>
        </DropdownMenuGroup>
        {columns.map((column) => {
          const visible = !hidden.has(column.name)
          // Never let the user hide the last visible column.
          const lockOn = visible && visibleCount <= 1
          return (
            <DropdownMenuCheckboxItem
              key={column.name}
              checked={visible}
              disabled={lockOn}
              onCheckedChange={() => onToggle(column.name)}
            >
              {fieldLabel(column)}
            </DropdownMenuCheckboxItem>
          )
        })}
        {(hasManagedVisible || hidden.size > 0) && <DropdownMenuSeparator />}
        {hasManagedVisible && (
          <DropdownMenuItem onClick={onHideManaged}>
            Hide database-managed columns
          </DropdownMenuItem>
        )}
        {hidden.size > 0 && (
          <DropdownMenuItem onClick={onReset}>Show all columns</DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function DensityMenu({
  density,
  onChange,
}: {
  density: Density
  onChange: (density: Density) => void
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<Button variant="outline" size="icon-sm" aria-label="Row density" />}
      >
        <Rows3Icon />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-40">
        <DropdownMenuGroup>
          <DropdownMenuLabel>Row density</DropdownMenuLabel>
        </DropdownMenuGroup>
        <DropdownMenuRadioGroup value={density} onValueChange={(v) => onChange(v as Density)}>
          <DropdownMenuRadioItem value="comfortable">Comfortable</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="compact">Compact</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
