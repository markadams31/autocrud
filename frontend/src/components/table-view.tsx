/**
 * table-view.tsx — Everything for one selected table.
 *
 * Reads its view (search, sort, page, page size, filters) from the URL via the
 * shared controller, so the view is deep-linkable; layout preferences (column
 * visibility, density) come from localStorage. Composes the toolbar, grid
 * controls, filter bar, grid, pagination, slide-over form, and delete dialog.
 * Mounted with a key of `${schema}.${table}` by the shell, so transient panel
 * state resets cleanly when the user switches tables.
 */

import { useEffect, useState } from 'react'
import { AnimatePresence } from 'motion/react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  DatabaseIcon,
  DownloadIcon,
  Loader2Icon,
  PlusIcon,
  SearchXIcon,
  UploadIcon,
} from 'lucide-react'

import { AnimatedNumber } from '@/components/animated-number'
import { BulkActionBar } from '@/components/bulk-action-bar'
import { BulkEditForm, BULK_EDIT_TITLE_ID } from '@/components/bulk-edit-form'
import { DataTable } from '@/components/data-table'
import { DeleteDialog } from '@/components/delete-dialog'
import { ImportDialog } from '@/components/import-dialog'
import { FilterBar } from '@/components/filter-bar'
import { ColumnsMenu, DensityMenu } from '@/components/grid-controls'
import { PaginationBar } from '@/components/pagination-bar'
import { RecordDetail } from '@/components/record-detail'
import { RecordForm, PANEL_TITLE_ID } from '@/components/record-form'
import { SlideOver } from '@/components/slide-over'
import { TableToolbar } from '@/components/table-toolbar'
import { useCelebrate } from '@/components/confetti'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { EmptyState, ErrorState } from '@/components/states'
import {
  queryKeys,
  useBulkDelete,
  useForeignKeyLabels,
  useRows,
  useTableMeta,
  useTables,
  useUpdateRow,
} from '@/hooks/queries'
import { useUndoableDelete } from '@/hooks/use-undoable-delete'
import { useDebouncedValue } from '@/hooks/use-debounced-value'
import { useDensity, useHiddenColumns } from '@/hooks/use-table-prefs'
import { useRowSelection } from '@/hooks/use-row-selection'
import { useCsvExport } from '@/hooks/use-csv-export'
import {
  PAGE_SIZE_OPTIONS,
  type ColumnFilter,
  type UrlController,
} from '@/hooks/use-url-state'
import { api } from '@/lib/api'
import { isConstraintViolation, messageFor } from '@/lib/errors'
import { trackEvent } from '@/lib/telemetry'
import type {
  BulkDeleteRequest,
  BulkUpdateRequest,
  ColumnMeta,
  FilterSpec,
  QueryRequest,
  Row,
  TablePermissions,
} from '@/types'

const NO_PERMISSIONS: TablePermissions = { insert: false, update: false, delete: false }

// Operators whose value is matched as text (kept as-is); the rest are coerced
// to the column's JSON type before being sent to the API.
const LIKE_OPS = new Set(['contains', 'startswith', 'endswith'])

interface TableViewProps {
  schema: string
  table: string
  url: UrlController
}

type PanelState =
  | { mode: 'create' }
  | { mode: 'edit'; row: Row }
  | { mode: 'view'; row: Row }
  | null

/** Coerce one raw filter string to the JSON type the column expects. */
function coerceValue(col: ColumnMeta, value: string): unknown {
  if (col.field_type === 'boolean') return value === 'true'
  if (col.field_type === 'integer' || col.field_type === 'number') {
    const n = Number(value)
    return Number.isFinite(n) ? n : value
  }
  // decimal stays a string to preserve precision; text/date/time pass through.
  return value
}

/**
 * Build the API filter payload from the URL's operator filters. Incomplete
 * filters (a value-requiring operator with nothing typed, or a half-entered
 * range) are dropped so they don't constrain the query while being edited.
 */
function buildApiFilters(
  raw: Record<string, ColumnFilter>,
  columns: ColumnMeta[],
): Record<string, FilterSpec> {
  const byName = new Map(columns.map((c) => [c.name, c]))
  const out: Record<string, FilterSpec> = {}
  for (const [name, { op, value }] of Object.entries(raw)) {
    const col = byName.get(name)
    if (!col) continue
    if (op === 'isnull' || op === 'notnull') {
      out[name] = { op, value: null }
    } else if (op === 'between') {
      const [lo = '', hi = ''] = value.split('..')
      if (lo === '' || hi === '') continue
      out[name] = { op, value: [coerceValue(col, lo), coerceValue(col, hi)] }
    } else if (value !== '') {
      out[name] = { op, value: LIKE_OPS.has(op) ? value : coerceValue(col, value) }
    }
  }
  return out
}

export function TableView({ schema, table, url }: TableViewProps) {
  const [panel, setPanel] = useState<PanelState>(null)
  const [importOpen, setImportOpen] = useState(false)
  // Encoded PKs of rows that were just created/updated — they flash briefly.
  const [flashKeys, setFlashKeys] = useState<Set<string>>(() => new Set())

  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false)
  // The bulk-edit slide-over. The nonce is bumped each time it opens so the form
  // remounts fresh (no fields carried over from a previous edit).
  const [bulkEditOpen, setBulkEditOpen] = useState(false)
  const [bulkEditNonce, setBulkEditNonce] = useState(0)

  // Retain the last panel so the content slides/fades out intact during the
  // close animation rather than blanking the instant state goes null.
  const [retainedPanel, setRetainedPanel] = useState<Exclude<PanelState, null> | null>(null)

  const debouncedSearch = useDebouncedValue(url.search, 300)
  const debouncedFilters = useDebouncedValue(url.filters, 300)
  // Stable, comparable key for the filter object — used to reset selection when
  // the query changes (a raw object would be a new reference every render).
  const filtersKey = JSON.stringify(debouncedFilters)

  const { density, setDensity } = useDensity()
  const {
    hidden,
    toggle: toggleColumn,
    reset: resetColumns,
    replace: replaceHiddenColumns,
  } = useHiddenColumns(schema, table)

  const metaQuery = useTableMeta(schema, table)
  const meta = metaQuery.data
  const { labels: fkLabels } = useForeignKeyLabels(meta)

  // Permissions come from the table list; deduped with the sidebar's query.
  const tablesQuery = useTables(schema)
  const permissions =
    tablesQuery.data?.find((t) => t.name === table)?.permissions ?? NO_PERMISSIONS

  const columns = meta?.columns ?? []
  const visibleColumns = columns.filter((c) => !hidden.has(c.name))

  const request: QueryRequest = {
    search: debouncedSearch,
    filters: meta ? buildApiFilters(debouncedFilters, columns) : {},
    sort: url.sort,
    page: url.page,
    page_size: url.pageSize,
  }
  const rowsQuery = useRows(schema, table, request)

  const { remove: removeRow, removeMany: removeRows } = useUndoableDelete(
    schema,
    table,
    meta?.primary_key ?? [],
    meta?.concurrency_token,
  )
  const update = useUpdateRow(schema, table, meta?.primary_key ?? [], meta?.concurrency_token)
  const bulkDelete = useBulkDelete(schema, table)
  const celebrate = useCelebrate()
  const queryClient = useQueryClient()

  const rowsData = rowsQuery.data
  const rows = rowsData?.data ?? []
  const total = rowsData?.total ?? 0
  const primaryKey = meta?.primary_key ?? []

  // Bulk-action row selection and CSV export, each extracted into a focused hook
  // so this component stays an orchestrator. Selection resets whenever the query
  // or page changes (the loaded rows change underneath it); the table switching
  // is handled by the shell re-keying this component.
  const selection = useRowSelection(
    rows,
    primaryKey,
    total,
    JSON.stringify([debouncedSearch, filtersKey, url.page, url.pageSize]),
  )
  const csvExport = useCsvExport({
    schema,
    table,
    meta,
    request,
    rows,
    total,
    visibleColumns,
    fkLabels,
  })

  // Prefetch the adjacent pages so paging is instant. The rows query result
  // changing (new page/search/sort/filters) re-runs this against the new view.
  useEffect(() => {
    const data = rowsQuery.data
    if (!data) return
    const prefetch = (page: number) => {
      if (page < 1 || page > data.pages || page === url.page) return
      const req = { ...request, page }
      queryClient.prefetchQuery({
        queryKey: queryKeys.rows(schema, table, req),
        queryFn: () => api.query(schema, table, req),
        staleTime: 30_000,
      })
    }
    prefetch(url.page + 1)
    prefetch(url.page - 1)
    // `request` is rebuilt each render from these inputs; depend on the result.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rowsQuery.data, url.page, schema, table])

  useEffect(() => {
    if (panel) setRetainedPanel(panel)
  }, [panel])

  // Hide every database-managed (non-editable) column at once — identity keys,
  // computed columns, server defaults, audit stamps — leaving the columns a user
  // actually edits. Reversible via "Show all columns".
  function hideManagedColumns() {
    const next = new Set(hidden)
    for (const c of columns) if (!c.editable) next.add(c.name)
    // Never hide literally every column.
    if (columns.length > 0 && columns.every((c) => next.has(c.name))) {
      next.delete(columns[0].name)
    }
    replaceHiddenColumns(next)
  }

  function handleSort(column: string) {
    url.setSort(
      url.sort.column === column
        ? { column, direction: url.sort.direction === 'asc' ? 'desc' : 'asc' }
        : { column, direction: 'asc' },
    )
  }

  function rowLabel(row: Row): string {
    const display = meta?.display_column
    if (display && row[display] != null) return String(row[display])
    if (meta) return meta.primary_key.map((k) => row[k]).join(', ')
    return 'this record'
  }

  // Single-row delete is instant + undoable (no confirm modal): the row leaves
  // the grid at once, with a 5s "Undo" toast as the safety net. Bulk delete keeps
  // its confirmation, since it's set-based and harder to reverse.
  function handleDelete(row: Row) {
    removeRow(row, { label: rowLabel(row) })
    setPanel(null) // close the detail/edit panel if it was open for this row
  }

  // ── Meta-level states ──────────────────────────────────────────────────────
  if (metaQuery.isError) {
    return (
      <div className="flex h-full items-center justify-center">
        <ErrorState
          error={metaQuery.error}
          action={
            <Button variant="outline" onClick={() => metaQuery.refetch()}>
              Try again
            </Button>
          }
        />
      </div>
    )
  }

  const loadingRows = metaQuery.isLoading || rowsQuery.isLoading
  const fetching = rowsQuery.isFetching && !rowsQuery.isLoading
  const hasQuery = Boolean(debouncedSearch) || Object.keys(url.filters).length > 0

  // ── Selection ────────────────────────────────────────────────────────────
  // Selection drives both bulk actions, so it's offered when the user can do
  // either; the action bar then shows only the buttons they're allowed. The
  // selection state itself lives in useRowSelection (above).
  const canBulkEdit = permissions.update
  const canBulkDelete = permissions.delete
  const canSelect = canBulkEdit || canBulkDelete
  const showBulkBar = canSelect && selection.effectiveCount > 0

  // Briefly flag a just-created/updated row so the grid flashes it. The key is
  // dropped after the animation so a later change to the same row flashes again.
  function flashRow(row: Row) {
    const key = selection.keyOf(row)
    setFlashKeys((prev) => new Set(prev).add(key))
    window.setTimeout(() => {
      setFlashKeys((prev) => {
        if (!prev.has(key)) return prev
        const next = new Set(prev)
        next.delete(key)
        return next
      })
    }, 1300)
  }

  // Inline cell edit → optimistic single-field update + a confirming flash.
  function handleCellEdit(row: Row, column: ColumnMeta, value: unknown) {
    update.mutate(
      { row, values: { [column.name]: value } },
      {
        onSuccess: () => flashRow(row),
        onError: (err) => {
          toast.error(messageFor(err, 'Could not save the change.'))
          // The optimistic cell update just rolled back — record it so
          // cache-reconciliation failures are visible in telemetry.
          trackEvent('optimistic_rollback', {
            op: 'cell_edit',
            table,
            code: isConstraintViolation(err) ? 'constraint' : 'other',
          })
        },
      },
    )
  }

  // Explicit selections delete instantly with Undo; set-based "all matching"
  // still confirms (we can't hold those rows to restore on undo).
  function handleBulkDelete() {
    if (!meta) return
    if (selection.allMatching) {
      setBulkDeleteOpen(true)
      return
    }
    removeRows(selection.selectedRows)
    selection.clear()
  }

  // The selection as a server-side scope: either every matching row (the grid's
  // search/filters, re-evaluated server-side) or the explicit ticked PKs. Shared
  // by bulk delete and bulk update so both act on exactly the same set.
  function selectionScope() {
    return selection.allMatching
      ? {
          all_matching: true,
          search: debouncedSearch,
          filters: buildApiFilters(debouncedFilters, columns),
        }
      : { ids: selection.selectedRows.map((r) => meta!.primary_key.map((k) => r[k])) }
  }

  function openBulkEdit() {
    setBulkEditNonce((n) => n + 1)
    setBulkEditOpen(true)
  }

  function bulkUpdateRequest(values: Row): BulkUpdateRequest {
    return { ...selectionScope(), values }
  }

  function confirmBulkDelete() {
    if (!meta) return
    bulkDelete.mutate(selectionScope() as BulkDeleteRequest, {
      onSuccess: (res) => {
        celebrate()
        toast.success(`Deleted ${res.deleted} record${res.deleted === 1 ? '' : 's'}.`)
        selection.clear()
        setBulkDeleteOpen(false)
      },
      onError: (err) => {
        toast.error(
          isConstraintViolation(err)
            ? 'Some of these records are still referenced by other records, so none were deleted.'
            : messageFor(err, 'Could not delete the selected records.'),
        )
      },
    })
  }

  return (
    <div className="flex h-full flex-col gap-4 p-5">
      {/* Title */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex items-center gap-2">
            <h1 className="truncate font-heading text-lg font-semibold">{table}</h1>
            <Badge variant="outline" className="font-normal text-muted-foreground">
              {schema}
            </Badge>
            {rowsData && (
              <span className="text-sm text-muted-foreground tabular-nums">
                <AnimatedNumber value={rowsData.total} />{' '}
                {rowsData.total === 1 ? 'record' : 'records'}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Toolbar */}
      <TableToolbar
        search={url.search}
        onSearchChange={url.setSearch}
        canInsert={permissions.insert}
        onNew={() => setPanel({ mode: 'create' })}
        actions={
          <>
            <ColumnsMenu
              columns={columns}
              hidden={hidden}
              onToggle={toggleColumn}
              onReset={resetColumns}
              onHideManaged={hideManagedColumns}
            />
            <DensityMenu density={density} onChange={setDensity} />
            {/* When everything matching is already on this page, "this page" and
                "all rows" are identical — so just offer a single Export. Only
                split into a menu when there are rows beyond the current page. */}
            {total > rows.length ? (
              <DropdownMenu>
                <DropdownMenuTrigger
                  render={
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={rows.length === 0 || csvExport.exporting}
                      title="Export to CSV"
                    />
                  }
                >
                  {csvExport.exporting ? (
                    <Loader2Icon className="animate-spin" />
                  ) : (
                    <DownloadIcon />
                  )}
                  Export
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={csvExport.exportPage}>
                    This page ({rows.length})
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={csvExport.exportAll}>
                    All {total.toLocaleString()} rows
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            ) : (
              <Button
                variant="outline"
                size="sm"
                onClick={csvExport.exportPage}
                disabled={rows.length === 0}
                title="Export to CSV"
              >
                <DownloadIcon />
                Export
              </Button>
            )}
            {permissions.insert && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setImportOpen(true)}
                title="Import rows from a CSV file"
              >
                <UploadIcon />
                Import
              </Button>
            )}
          </>
        }
      />

      {/* Filters */}
      {columns.length > 0 && (
        <FilterBar
          columns={columns}
          filters={url.filters}
          onChange={url.setFilters}
          schema={schema}
          table={table}
        />
      )}

      {/* Bulk actions — shown only while rows are selected */}
      <AnimatePresence>
        {showBulkBar && (
          <BulkActionBar
            count={selection.effectiveCount}
            allMatching={selection.allMatching}
            total={total}
            canSelectAllMatching={selection.canSelectAllMatching}
            onSelectAllMatching={selection.selectAllMatching}
            onClear={selection.clear}
            onEdit={canBulkEdit ? openBulkEdit : undefined}
            onDelete={canBulkDelete ? handleBulkDelete : undefined}
          />
        )}
      </AnimatePresence>

      {/* Grid */}
      <div className="relative min-h-0 flex-1 overflow-hidden rounded-xl border bg-card shadow-sm">
        {/* Background-fetch indicator */}
        <div className={cnFetchBar(fetching)} aria-hidden />
        {rowsQuery.isError ? (
          <div className="flex h-full items-center justify-center">
            <ErrorState
              error={rowsQuery.error}
              action={
                <Button variant="outline" onClick={() => rowsQuery.refetch()}>
                  Try again
                </Button>
              }
            />
          </div>
        ) : !loadingRows && rows.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            {hasQuery ? (
              <EmptyState
                icon={SearchXIcon}
                title="No matches"
                description="No records match the current search and filters."
                action={
                  <Button
                    variant="outline"
                    onClick={() => {
                      url.setSearch('')
                      url.setFilters({})
                    }}
                  >
                    Clear search & filters
                  </Button>
                }
              />
            ) : (
              <EmptyState
                icon={DatabaseIcon}
                title="No records yet"
                description={
                  permissions.insert
                    ? 'This table is empty. Create the first record to get started.'
                    : 'This table is empty.'
                }
                action={
                  permissions.insert ? (
                    <Button onClick={() => setPanel({ mode: 'create' })}>
                      <PlusIcon />
                      New record
                    </Button>
                  ) : undefined
                }
              />
            )}
          </div>
        ) : (
          <DataTable
            columns={visibleColumns}
            rows={rows}
            fkLabels={fkLabels}
            sort={url.sort}
            onSort={handleSort}
            loading={loadingRows}
            density={density}
            permissions={permissions}
            onRowClick={(row) => setPanel({ mode: 'view', row })}
            onEdit={(row) => setPanel({ mode: 'edit', row })}
            onDelete={handleDelete}
            recentlyChanged={(row) => flashKeys.has(selection.keyOf(row))}
            schema={schema}
            table={table}
            onCellEdit={permissions.update ? handleCellEdit : undefined}
            selection={
              canSelect
                ? {
                    isSelected: selection.isSelected,
                    onToggleRow: selection.toggleRow,
                    onToggleAllLoaded: selection.toggleAllLoaded,
                  }
                : undefined
            }
          />
        )}
      </div>

      {/* Pagination */}
      {rowsData && rowsData.total > 0 && (
        <PaginationBar
          page={rowsData.page}
          pages={rowsData.pages}
          total={rowsData.total}
          pageSize={rowsData.page_size}
          pageSizeOptions={PAGE_SIZE_OPTIONS}
          onPageChange={url.setPage}
          onPageSizeChange={url.setPageSize}
        />
      )}

      {/* View / create / edit panel */}
      <SlideOver
        open={panel !== null}
        onClose={() => setPanel(null)}
        labelledBy={PANEL_TITLE_ID}
      >
        {retainedPanel && meta && (
          retainedPanel.mode === 'view' ? (
            <RecordDetail
              meta={meta}
              row={retainedPanel.row}
              fkLabels={fkLabels}
              permissions={permissions}
              onEdit={() => setPanel({ mode: 'edit', row: retainedPanel.row })}
              onDelete={() => handleDelete(retainedPanel.row)}
              onClose={() => setPanel(null)}
            />
          ) : (
            <RecordForm
              key={
                retainedPanel.mode === 'edit'
                  ? `edit-${meta.primary_key.map((k) => retainedPanel.row[k]).join(',')}`
                  : 'create'
              }
              meta={meta}
              mode={retainedPanel.mode}
              row={retainedPanel.mode === 'edit' ? retainedPanel.row : undefined}
              onClose={() => setPanel(null)}
              onSaved={(saved) => {
                setPanel(null)
                if (saved) flashRow(saved)
              }}
            />
          )
        )}
      </SlideOver>

      {/* Delete confirmation — bulk */}
      <DeleteDialog
        open={bulkDeleteOpen}
        onOpenChange={(open) => !open && setBulkDeleteOpen(false)}
        count={selection.effectiveCount}
        loading={bulkDelete.isPending}
        onConfirm={confirmBulkDelete}
      />

      {/* Import rows from a CSV */}
      {meta && (
        <ImportDialog
          open={importOpen}
          onOpenChange={setImportOpen}
          meta={meta}
          fkLabels={fkLabels}
          onImported={() => rowsQuery.refetch()}
        />
      )}

      {/* Bulk edit — one change → many rows */}
      <SlideOver
        open={bulkEditOpen}
        onClose={() => setBulkEditOpen(false)}
        labelledBy={BULK_EDIT_TITLE_ID}
      >
        {meta && (
          <BulkEditForm
            key={`bulk-edit-${bulkEditNonce}`}
            meta={meta}
            count={selection.effectiveCount}
            buildRequest={bulkUpdateRequest}
            onApplied={() => {
              selection.clear()
              setBulkEditOpen(false)
            }}
            onClose={() => setBulkEditOpen(false)}
          />
        )}
      </SlideOver>
    </div>
  )
}

/** Thin indeterminate bar shown along the top of the grid during background fetches. */
function cnFetchBar(fetching: boolean): string {
  return [
    'pointer-events-none absolute inset-x-0 top-0 z-40 h-0.5 origin-left bg-primary transition-opacity duration-200',
    fetching ? 'animate-pulse opacity-100' : 'opacity-0',
  ].join(' ')
}
