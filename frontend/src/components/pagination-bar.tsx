/**
 * pagination-bar.tsx — Page navigation, a rows-per-page selector, and the
 * row-count summary below the grid.
 */

import { ChevronLeftIcon, ChevronRightIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

const integerFormat = new Intl.NumberFormat()

interface PaginationBarProps {
  page: number
  pages: number
  total: number
  pageSize: number
  pageSizeOptions: number[]
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
}

export function PaginationBar({
  page,
  pages,
  total,
  pageSize,
  pageSizeOptions,
  onPageChange,
  onPageSizeChange,
}: PaginationBarProps) {
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1
  const to = Math.min(page * pageSize, total)
  const sizeItems = pageSizeOptions.map((n) => ({ value: String(n), label: String(n) }))

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-muted-foreground">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1.5">
          <span>Rows</span>
          <Select
            items={sizeItems}
            value={String(pageSize)}
            onValueChange={(v) => v && onPageSizeChange(Number(v))}
          >
            <SelectTrigger size="sm" className="h-7 w-[4.25rem]" aria-label="Rows per page">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {sizeItems.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <p className="tabular-nums">
          {total === 0 ? (
            'No records'
          ) : (
            <>
              <span className="font-medium text-foreground">{integerFormat.format(from)}</span>
              {'–'}
              <span className="font-medium text-foreground">{integerFormat.format(to)}</span>
              {' of '}
              <span className="font-medium text-foreground">{integerFormat.format(total)}</span>
            </>
          )}
        </p>
      </div>

      <div className="flex items-center gap-2">
        <span className="tabular-nums">
          Page {integerFormat.format(page)} of {integerFormat.format(Math.max(pages, 1))}
        </span>
        <div className="flex items-center gap-1">
          <Button
            variant="outline"
            size="icon-sm"
            onClick={() => onPageChange(page - 1)}
            disabled={page <= 1}
            aria-label="Previous page"
          >
            <ChevronLeftIcon />
          </Button>
          <Button
            variant="outline"
            size="icon-sm"
            onClick={() => onPageChange(page + 1)}
            disabled={page >= pages}
            aria-label="Next page"
          >
            <ChevronRightIcon />
          </Button>
        </div>
      </div>
    </div>
  )
}
