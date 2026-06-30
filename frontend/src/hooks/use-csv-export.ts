/**
 * use-csv-export.ts — CSV export of the current grid view.
 *
 * Two exports, both building the CSV client-side so foreign keys resolve to the
 * same labels shown on screen:
 *   - exportPage: the rows currently loaded (the visible page).
 *   - exportAll:  every row matching the current search/filters, walked page by
 *                 page in the current sort order, bounded by EXPORT_MAX_ROWS.
 *
 * Extracted from table-view to keep the orchestration component focused; the
 * export flow (pagination walk, bound check, toasts) lives in one place.
 */
import { useState } from 'react'
import { toast } from 'sonner'

import type { FkLabelMap } from '@/hooks/queries'
import { api } from '@/lib/api'
import { downloadCsv, toCsv } from '@/lib/csv'
import { messageFor } from '@/lib/errors'
import type { ColumnMeta, QueryRequest, Row, TableMeta } from '@/types'

// Upper bound on a full-result export. The browser pulls the whole set into
// memory and builds the CSV client-side (so foreign keys resolve to labels), so
// a request over this is refused rather than hanging the tab.
export const EXPORT_MAX_ROWS = 10_000

interface CsvExportParams {
  schema: string
  table: string
  meta: TableMeta | undefined
  /** The active query (search/filters/sort) — the "all rows" export walks its pages. */
  request: QueryRequest
  /** The rows currently loaded on screen (the page export). */
  rows: Row[]
  /** Total rows matching the current query (drives the "all rows" bound check). */
  total: number
  visibleColumns: ColumnMeta[]
  fkLabels: FkLabelMap
}

export interface CsvExport {
  exporting: boolean
  exportPage: () => void
  exportAll: () => Promise<void>
}

export function useCsvExport(params: CsvExportParams): CsvExport {
  const { schema, table, meta, request, rows, total, visibleColumns, fkLabels } = params
  const [exporting, setExporting] = useState(false)

  // Export exactly what's on screen: the current page's rows and the
  // currently-visible columns.
  function exportPage() {
    if (!meta || rows.length === 0) return
    downloadCsv(`${schema}.${table}.csv`, toCsv(visibleColumns, rows, fkLabels))
    toast.success(`Exported ${rows.length} row${rows.length === 1 ? '' : 's'}.`)
  }

  // Export every row matching the current search and filters — not just the page
  // on screen. The grid endpoint is paginated, so we walk the pages (in the
  // current sort order) and build the CSV client-side, which keeps foreign keys
  // resolved to their labels exactly like the on-screen export.
  async function exportAll() {
    if (!meta) return
    if (total === 0) return
    if (total > EXPORT_MAX_ROWS) {
      toast.error(
        `That's ${total.toLocaleString()} rows — over the ${EXPORT_MAX_ROWS.toLocaleString()} ` +
          `export limit. Narrow your search or filters and try again.`,
      )
      return
    }

    setExporting(true)
    try {
      const pageSize = 500
      const pageCount = Math.ceil(total / pageSize)
      const all: Row[] = []
      for (let page = 1; page <= pageCount; page++) {
        const res = await api.query(schema, table, { ...request, page, page_size: pageSize })
        all.push(...res.data)
      }
      downloadCsv(`${schema}.${table}.csv`, toCsv(visibleColumns, all, fkLabels))
      toast.success(`Exported ${all.length} row${all.length === 1 ? '' : 's'}.`)
    } catch (err) {
      toast.error(messageFor(err, 'Could not export the rows.'))
    } finally {
      setExporting(false)
    }
  }

  return { exporting, exportPage, exportAll }
}
