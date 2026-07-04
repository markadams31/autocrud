/**
 * import-dialog.tsx — Bulk-create rows from a filled-in CSV template.
 *
 * Three beats in one dialog: download a template (the editable column names as a
 * header row), choose a filled-in file, then a read-only preview that resolves
 * and validates every cell — foreign keys by label or id, types coerced, blanks
 * left to defaults — and highlights anything wrong. Import is blocked until the
 * preview is clean; the whole batch is then created atomically on the server, so
 * either every row lands or none do. Server-side problems the browser can't see
 * (a duplicate key) come back attributed to a row and are highlighted too.
 */

import { useState } from 'react'
import { toast } from 'sonner'
import {
  AlertTriangleIcon,
  DownloadIcon,
  FileUpIcon,
  UploadIcon,
} from 'lucide-react'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { SaveButton } from '@/components/save-button'
import { useCelebrate } from '@/components/confetti'
import { useBulkCreate } from '@/hooks/queries'
import { sizeBucket, trackEvent } from '@/lib/telemetry'
import type { FkLabelMap } from '@/hooks/queries'
import { ApiError } from '@/lib/api'
import { downloadCsv } from '@/lib/csv'
import { messageFor } from '@/lib/errors'
import {
  analyzeImport,
  applyServerRowErrors,
  buildTemplate,
  isImportable,
  parseCsv,
  type ImportAnalysis,
} from '@/lib/csv-import'
import { fieldLabel } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { Row, TableMeta } from '@/types'

const PREVIEW_LIMIT = 100
const ERROR_LIST_LIMIT = 50

interface ImportDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  meta: TableMeta
  fkLabels: FkLabelMap
  /** Called after a successful import so the parent can refetch/notify. */
  onImported: (created: number) => void
}

export function ImportDialog({ open, onOpenChange, meta, fkLabels, onImported }: ImportDialogProps) {
  const [analysis, setAnalysis] = useState<ImportAnalysis | null>(null)
  const [fileName, setFileName] = useState('')
  const [parseError, setParseError] = useState<string | null>(null)
  // A row-level server error (e.g. a duplicate key) that isn't tied to a cell.
  const [banner, setBanner] = useState<string | null>(null)
  const [constraintRow, setConstraintRow] = useState<number | null>(null)

  const bulkCreate = useBulkCreate(meta.schema, meta.name)
  const celebrate = useCelebrate()

  function reset() {
    setAnalysis(null)
    setFileName('')
    setParseError(null)
    setBanner(null)
    setConstraintRow(null)
  }

  function handleOpenChange(next: boolean) {
    if (!next) reset()
    onOpenChange(next)
  }

  function downloadTemplate() {
    downloadCsv(`${meta.schema}.${meta.name}-template.csv`, buildTemplate(meta.columns))
  }

  async function handleFile(file: File) {
    setBanner(null)
    setConstraintRow(null)
    setParseError(null)
    setFileName(file.name)
    try {
      const text = await file.text()
      setAnalysis(analyzeImport(meta.columns, parseCsv(text), fkLabels))
    } catch {
      setAnalysis(null)
      setParseError('Could not read that file. Make sure it’s a CSV.')
    }
  }

  function handleImport() {
    if (!analysis || !isImportable(analysis)) return
    setBanner(null)
    setConstraintRow(null)
    const rows: Row[] = analysis.rows.map((r) => r.values)

    bulkCreate.mutate(
      { rows },
      {
        onSuccess: (res) => {
          trackEvent('csv_import', {
            schema: meta.schema, table: meta.name,
            count: sizeBucket(res.created), outcome: 'ok',
          })
          celebrate()
          toast.success(`Imported ${res.created} ${res.created === 1 ? 'record' : 'records'}.`)
          onImported(res.created)
          handleOpenChange(false)
        },
        onError: (err) => {
          const rejected = err instanceof ApiError && (!!err.rows || err.row != null)
          trackEvent('csv_import', {
            schema: meta.schema, table: meta.name,
            count: sizeBucket(rows.length),
            outcome: rejected ? 'rejected' : 'error',
            failed_rows:
              err instanceof ApiError ? (err.rows ? Object.keys(err.rows).length : err.row != null ? 1 : 0) : 0,
            code: err instanceof ApiError ? err.code : 'error',
          })
          if (err instanceof ApiError && err.rows) {
            setAnalysis((a) => (a ? applyServerRowErrors(a, err.rows!) : a))
            toast.error('Some rows need fixing — see the highlighted cells.')
          } else if (err instanceof ApiError && err.row != null) {
            setConstraintRow(err.row)
            setBanner(err.message)
            toast.error(err.message)
          } else {
            const msg = messageFor(err, 'Could not import the rows.')
            setBanner(msg)
            toast.error(msg)
          }
        },
      },
    )
  }

  const importable = analysis != null && isImportable(analysis)
  const saving = bulkCreate.isPending

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Import CSV</DialogTitle>
          <DialogDescription>
            Download the template, fill it in, then upload it. Every row is created together —
            if anything is wrong, nothing is imported.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" variant="outline" size="sm" onClick={downloadTemplate}>
            <DownloadIcon />
            Download template
          </Button>

          <label className="inline-flex">
            <input
              type="file"
              accept=".csv,text/csv"
              aria-label="CSV file"
              className="sr-only"
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) void handleFile(file)
                e.target.value = '' // allow re-selecting the same file
              }}
            />
            <span
              className={cn(
                'inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-md border px-3 text-sm font-medium',
                'transition-colors hover:bg-accent hover:text-accent-foreground',
              )}
            >
              <FileUpIcon className="size-4" />
              {fileName ? 'Choose a different file' : 'Choose CSV file'}
            </span>
          </label>

          {fileName && <span className="truncate text-sm text-muted-foreground">{fileName}</span>}
        </div>

        {parseError && (
          <p className="flex items-center gap-1.5 text-sm text-destructive">
            <AlertTriangleIcon className="size-4" />
            {parseError}
          </p>
        )}

        {analysis && (
          <ImportPreview analysis={analysis} banner={banner} constraintRow={constraintRow} />
        )}

        <DialogFooter>
          <Button type="button" variant="ghost" onClick={() => handleOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <SaveButton
            type="button"
            loading={saving}
            disabled={!importable}
            onClick={handleImport}
          >
            <UploadIcon />
            {analysis ? `Import ${analysis.rows.length} ${analysis.rows.length === 1 ? 'row' : 'rows'}` : 'Import'}
          </SaveButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ImportPreview({
  analysis,
  banner,
  constraintRow,
}: {
  analysis: ImportAnalysis
  banner: string | null
  constraintRow: number | null
}) {
  const { columns, rows, unknownHeaders, missingRequired, errorCount } = analysis
  const shown = rows.slice(0, PREVIEW_LIMIT)

  // A flat, readable list of every cell error so the reason is visible up front
  // (not just a tooltip on the highlighted cell).
  const labelOf = new Map(columns.map((c) => [c.name, fieldLabel(c)]))
  const errorList = rows.flatMap((row) =>
    Object.entries(row.errors).map(([col, message]) => ({
      line: row.line,
      label: labelOf.get(col) ?? col,
      message,
    })),
  )
  const shownErrors = errorList.slice(0, ERROR_LIST_LIMIT)

  return (
    <div className="space-y-2">
      {/* Summary line */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <span className="font-medium tabular-nums">
          {rows.length} {rows.length === 1 ? 'row' : 'rows'}
        </span>
        {errorCount > 0 ? (
          <span className="text-destructive">
            {errorCount} {errorCount === 1 ? 'error' : 'errors'} to fix
          </span>
        ) : rows.length > 0 ? (
          <span className="text-success">Ready to import</span>
        ) : (
          <span className="text-muted-foreground">No rows found</span>
        )}
      </div>

      {/* Header-level warnings */}
      {missingRequired.length > 0 && (
        <p className="flex items-start gap-1.5 text-sm text-destructive">
          <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
          <span>
            Missing required column{missingRequired.length === 1 ? '' : 's'}:{' '}
            <span className="font-medium">{missingRequired.join(', ')}</span>. Add{' '}
            {missingRequired.length === 1 ? 'it' : 'them'} to the file and re-upload.
          </span>
        </p>
      )}
      {unknownHeaders.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Ignored unknown column{unknownHeaders.length === 1 ? '' : 's'}: {unknownHeaders.join(', ')}
        </p>
      )}
      {banner && (
        <p className="flex items-start gap-1.5 text-sm text-destructive">
          <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
          <span>{banner}</span>
        </p>
      )}

      {/* What's wrong, spelled out — so the user doesn't have to hover cells. */}
      {errorList.length > 0 && (
        <ul className="max-h-40 space-y-1 overflow-auto rounded-lg border border-destructive/30 bg-destructive/5 p-2 text-sm">
          {shownErrors.map((e, i) => (
            <li key={i} className="flex flex-wrap items-baseline gap-x-1.5">
              <span className="shrink-0 text-muted-foreground tabular-nums">Row {e.line}</span>
              <span className="shrink-0 font-medium">{e.label}:</span>
              <span className="text-destructive">{e.message}</span>
            </li>
          ))}
          {errorList.length > ERROR_LIST_LIMIT && (
            <li className="text-xs text-muted-foreground">
              …and {errorList.length - ERROR_LIST_LIMIT} more.
            </li>
          )}
        </ul>
      )}

      {/* Read-only preview table */}
      {columns.length > 0 && shown.length > 0 && (
        <div className="max-h-80 overflow-auto rounded-lg border">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="sticky top-0 bg-muted/80 backdrop-blur-sm">
                <th className="px-2 py-1.5 text-right text-xs font-medium text-muted-foreground">#</th>
                {columns.map((c) => (
                  <th key={c.name} className="px-2 py-1.5 text-left text-xs font-medium whitespace-nowrap">
                    {fieldLabel(c)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shown.map((row) => {
                const rowFlagged = constraintRow === row.line - 1
                return (
                  <tr
                    key={row.line}
                    className={cn('border-t', rowFlagged && 'bg-destructive/10')}
                  >
                    <td className="px-2 py-1 text-right text-xs text-muted-foreground tabular-nums">
                      {row.line}
                    </td>
                    {columns.map((c) => {
                      const error = row.errors[c.name]
                      return (
                        <td
                          key={c.name}
                          title={error || undefined}
                          className={cn(
                            'max-w-48 truncate px-2 py-1 whitespace-nowrap',
                            error && 'bg-destructive/10 text-destructive',
                          )}
                        >
                          {error ? row.display[c.name] || '—' : row.display[c.name] || (
                            <span className="text-muted-foreground/50">—</span>
                          )}
                        </td>
                      )
                    })}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {rows.length > PREVIEW_LIMIT && (
        <p className="text-xs text-muted-foreground">
          Showing the first {PREVIEW_LIMIT} of {rows.length} rows.
        </p>
      )}
    </div>
  )
}
