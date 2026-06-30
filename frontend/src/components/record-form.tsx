/**
 * record-form.tsx — A create/edit form generated entirely from table metadata.
 *
 * Editable columns become inputs (via FieldControl); the column's `required`,
 * `foreign_key`, and `is_audit` flags drive labels and badges. In edit mode the
 * database-owned columns (identity PK, computed values, audit stamps) are shown
 * in a separate read-only section so the user sees the full record without ever
 * being offered a control the API would reject.
 *
 * Validation is layered: a client check for required fields on create, then the
 * server's per-field VALIDATION_ERROR detail surfaced inline on the exact
 * inputs that failed.
 */

import { useState } from 'react'
import { motion } from 'motion/react'
import { toast } from 'sonner'
import { LockIcon, XIcon } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { FieldControl } from '@/components/field-control'
import { SaveButton } from '@/components/save-button'
import { useCelebrate } from '@/components/confetti'
import { useCreateRow, useUpdateRow } from '@/hooks/queries'
import { fieldErrors, isConflict, messageFor } from '@/lib/errors'
import { listContainer, listItem } from '@/lib/animations'
import { convertFieldValue, emptyFieldValue, toInputValue, type FormValue } from '@/lib/field-values'
import { fieldLabel, formatValue, NULL_DISPLAY } from '@/lib/format'
import { validationToast } from '@/lib/validation-toast'
import type { Row, TableMeta } from '@/types'

export const PANEL_TITLE_ID = 'record-panel-title'

const validation = validationToast('record-form-validation')

type FormState = Record<string, FormValue>

function buildInitialState(meta: TableMeta, row: Row | undefined, mode: 'create' | 'edit'): FormState {
  const state: FormState = {}
  for (const c of meta.columns) {
    if (!c.editable) continue
    state[c.name] = mode === 'edit' && row ? toInputValue(c, row) : emptyFieldValue(c)
  }
  return state
}

interface RecordFormProps {
  meta: TableMeta
  mode: 'create' | 'edit'
  row?: Row
  onClose: () => void
  /** Called after a successful save with the created/updated row. */
  onSaved: (saved?: Row) => void
}

export function RecordForm({ meta, mode, row, onClose, onSaved }: RecordFormProps) {
  const [initial] = useState<FormState>(() => buildInitialState(meta, row, mode))
  const [form, setForm] = useState<FormState>(initial)
  const [errors, setErrors] = useState<Record<string, string>>({})

  const create = useCreateRow(meta.schema, meta.name)
  const update = useUpdateRow(meta.schema, meta.name, meta.primary_key, meta.concurrency_token)
  const saving = create.isPending || update.isPending
  const celebrate = useCelebrate()

  const editableColumns = meta.columns.filter((c) => c.editable)
  const systemColumns = meta.columns.filter((c) => !c.editable)
  const firstFieldName = editableColumns[0]?.name

  function setValue(name: string, value: unknown) {
    setForm((prev) => ({ ...prev, [name]: value as FormValue }))
    // Clear a field's error as soon as the user edits it — and retire the global
    // "fix the highlighted fields" toast, which is now stale.
    if (errors[name]) validation.dismiss()
    setErrors((prev) => {
      if (!prev[name]) return prev
      const next = { ...prev }
      delete next[name]
      return next
    })
  }

  function handleError(err: unknown) {
    // A concurrency conflict means the row changed under us, so this form's data
    // is stale and resubmitting would clobber the newer version. Close it — the
    // mutation already refetched the grid — so the user reopens the latest row.
    if (isConflict(err)) {
      toast.error(messageFor(err, 'This record was changed by someone else.'))
      onClose()
      return
    }
    const fields = fieldErrors(err)
    if (fields) {
      setErrors(fields)
      validation.show()
    } else {
      toast.error(messageFor(err, 'Something went wrong. Please try again.'))
    }
  }

  function handleSuccess(saved: Row) {
    validation.dismiss()
    celebrate()
    toast.success(mode === 'create' ? 'Record created.' : 'Changes saved.')
    onSaved(saved)
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setErrors({})

    if (mode === 'create') {
      const missing: Record<string, string> = {}
      for (const c of editableColumns) {
        if (c.required) {
          const v = form[c.name]
          if (v === null || v === '') missing[c.name] = 'This field is required.'
        }
      }
      if (Object.keys(missing).length > 0) {
        setErrors(missing)
        return
      }

      const payload: Row = {}
      for (const c of editableColumns) {
        const v = convertFieldValue(c, form[c.name])
        if (v !== null) payload[c.name] = v
      }
      create.mutate(payload, { onSuccess: handleSuccess, onError: handleError })
      return
    }

    // edit — send only changed fields (partial PATCH semantics)
    const payload: Row = {}
    for (const c of editableColumns) {
      if (form[c.name] !== initial[c.name]) payload[c.name] = convertFieldValue(c, form[c.name])
    }
    if (Object.keys(payload).length === 0) {
      toast('No changes to save.')
      onClose()
      return
    }
    update.mutate(
      { row: row!, values: payload },
      { onSuccess: handleSuccess, onError: handleError },
    )
  }

  return (
    <form onSubmit={handleSubmit} className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 border-b px-5 py-4">
        <div className="space-y-0.5">
          <h2 id={PANEL_TITLE_ID} className="font-heading text-base font-semibold">
            {mode === 'create' ? 'New record' : 'Edit record'}
          </h2>
          <p className="text-sm text-muted-foreground">
            {meta.schema}.{meta.name}
          </p>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          onClick={onClose}
          aria-label="Close"
        >
          <XIcon />
        </Button>
      </div>

      {/* Fields */}
      <ScrollArea className="min-h-0 flex-1">
        <motion.div
          className="space-y-5 p-5"
          variants={listContainer}
          initial="hidden"
          animate="visible"
        >
          {editableColumns.map((column) => {
            const error = errors[column.name]
            const fieldId = `field-${column.name}`
            return (
              <motion.div key={column.name} variants={listItem} className="space-y-1.5">
                <Label htmlFor={fieldId} className="gap-1.5">
                  <span>{fieldLabel(column)}</span>
                  {column.required && (
                    <span className="text-destructive" aria-hidden>
                      *
                    </span>
                  )}
                  {column.foreign_key && (
                    <Badge variant="secondary" className="font-normal">
                      → {column.foreign_key.table}
                    </Badge>
                  )}
                </Label>
                <FieldControl
                  id={fieldId}
                  column={column}
                  value={form[column.name]}
                  onChange={(v) => setValue(column.name, v)}
                  schema={meta.schema}
                  table={meta.name}
                  invalid={!!error}
                  autoFocus={column.name === firstFieldName}
                />
                {error && <p className="text-xs text-destructive">{error}</p>}
              </motion.div>
            )
          })}

          {/* Read-only system fields (edit only) */}
          {mode === 'edit' && row && systemColumns.length > 0 && (
            <motion.div variants={listItem} className="@container space-y-3 pt-1">
              <Separator />
              <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <LockIcon className="size-3.5" />
                <span>System fields — managed by the database</span>
              </div>
              <dl className="grid grid-cols-1 gap-x-4 gap-y-2 @sm:grid-cols-2">
                {systemColumns.map((column) => {
                  const display = formatValue(row[column.name], column)
                  return (
                    <div key={column.name} className="min-w-0">
                      <dt className="truncate text-xs text-muted-foreground">
                        {fieldLabel(column)}
                      </dt>
                      <dd className="truncate text-sm tabular-nums">
                        {display ?? (
                          <span className="text-muted-foreground/60">{NULL_DISPLAY}</span>
                        )}
                      </dd>
                    </div>
                  )
                })}
              </dl>
            </motion.div>
          )}
        </motion.div>
      </ScrollArea>

      {/* Footer */}
      <div className="flex items-center justify-end gap-2 border-t bg-muted/30 px-5 py-3">
        <Button type="button" variant="ghost" onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <SaveButton loading={saving}>
          {mode === 'create' ? 'Create record' : 'Save changes'}
        </SaveButton>
      </div>
    </form>
  )
}
