/**
 * bulk-edit-form.tsx — "One change → many rows."
 *
 * The slide-over panel for a bulk update. The user adds one or more fields and
 * sets a value for each; on apply, every selected row (or every row matching the
 * query) gets those exact values in a single atomic UPDATE (see crud.py
 * bulk-update). Only the fields the user explicitly adds are written — every
 * other column is left untouched — so this is purely additive, never a full-row
 * overwrite. Field controls and value conversion are the same ones the
 * single-record form uses, so a bulk edit behaves identically to editing each
 * row by hand.
 */

import { useState } from 'react'
import { motion } from 'motion/react'
import { toast } from 'sonner'
import { PlusIcon, XIcon } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { FieldControl } from '@/components/field-control'
import { SaveButton } from '@/components/save-button'
import { useCelebrate } from '@/components/confetti'
import { useBulkUpdate } from '@/hooks/queries'
import { easeOutExpo } from '@/lib/animations'
import { fieldErrors, messageFor } from '@/lib/errors'
import { convertFieldValue, emptyFieldValue, type FormValue } from '@/lib/field-values'
import { fieldLabel } from '@/lib/format'
import { validationToast } from '@/lib/validation-toast'
import type { BulkUpdateRequest, Row, TableMeta } from '@/types'

export const BULK_EDIT_TITLE_ID = 'bulk-edit-panel-title'

const validation = validationToast('bulk-edit-validation')

interface BulkEditFormProps {
  meta: TableMeta
  /** How many rows the apply will affect (the effective selection size). */
  count: number
  /** Turn the collected values into the request body (carries the selection). */
  buildRequest: (values: Row) => BulkUpdateRequest
  /** Called after a successful apply — the parent clears selection and closes. */
  onApplied: () => void
  onClose: () => void
}

/** One field the user has chosen to set, in insertion order. */
interface ChosenField {
  name: string
  value: FormValue
}

export function BulkEditForm({ meta, count, buildRequest, onApplied, onClose }: BulkEditFormProps) {
  const [fields, setFields] = useState<ChosenField[]>([])
  const [errors, setErrors] = useState<Record<string, string>>({})

  const bulkUpdate = useBulkUpdate(meta.schema, meta.name)
  const celebrate = useCelebrate()
  const saving = bulkUpdate.isPending

  const byName = new Map(meta.columns.map((c) => [c.name, c]))
  const editable = meta.columns.filter((c) => c.editable)
  const chosen = new Set(fields.map((f) => f.name))
  const available = editable.filter((c) => !chosen.has(c.name))

  const noun = count === 1 ? 'record' : 'records'

  function addField(name: string) {
    const col = byName.get(name)
    if (!col) return
    setFields((prev) => [...prev, { name, value: emptyFieldValue(col) }])
  }

  function removeField(name: string) {
    setFields((prev) => prev.filter((f) => f.name !== name))
    clearError(name)
  }

  function setValue(name: string, value: unknown) {
    setFields((prev) => prev.map((f) => (f.name === name ? { ...f, value: value as FormValue } : f)))
    clearError(name)
  }

  function clearError(name: string) {
    if (errors[name]) validation.dismiss()
    setErrors((prev) => {
      if (!prev[name]) return prev
      const next = { ...prev }
      delete next[name]
      return next
    })
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (fields.length === 0) return
    setErrors({})

    // Client guard: a non-nullable column can't be cleared (the database would
    // reject it anyway — catch it here for an inline message).
    const missing: Record<string, string> = {}
    const values: Row = {}
    for (const f of fields) {
      const col = byName.get(f.name)!
      const converted = convertFieldValue(col, f.value)
      if (!col.nullable && converted === null) {
        missing[f.name] = 'This field cannot be empty.'
      }
      values[f.name] = converted
    }
    if (Object.keys(missing).length > 0) {
      setErrors(missing)
      return
    }

    bulkUpdate.mutate(buildRequest(values), {
      onSuccess: (res) => {
        validation.dismiss()
        celebrate()
        toast.success(`Updated ${res.updated} ${res.updated === 1 ? 'record' : 'records'}.`)
        onApplied()
      },
      onError: (err) => {
        const fields = fieldErrors(err)
        if (fields) {
          setErrors(fields)
          validation.show()
        } else {
          toast.error(messageFor(err, 'Could not update the selected records.'))
        }
      },
    })
  }

  return (
    <form onSubmit={handleSubmit} className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 border-b px-5 py-4">
        <div className="space-y-0.5">
          <h2 id={BULK_EDIT_TITLE_ID} className="font-heading text-base font-semibold">
            Edit {count} {noun}
          </h2>
          <p className="text-sm text-muted-foreground">
            {meta.schema}.{meta.name}
          </p>
        </div>
        <Button type="button" variant="ghost" size="icon-sm" onClick={onClose} aria-label="Close">
          <XIcon />
        </Button>
      </div>

      {/* Fields */}
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-5 p-5">
          <p className="text-sm text-muted-foreground">
            Add the fields you want to change. Each new value is applied to all{' '}
            <span className="font-medium text-foreground">{count} {noun}</span>; fields you don’t
            add are left untouched.
          </p>

          {available.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button type="button" variant="outline" size="sm" />}>
                <PlusIcon />
                Add field
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="max-h-72 overflow-y-auto">
                {available.map((column) => (
                  <DropdownMenuItem key={column.name} onClick={() => addField(column.name)}>
                    {fieldLabel(column)}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}

          <div className="space-y-5">
            {fields.map((f) => {
              const column = byName.get(f.name)!
              const error = errors[f.name]
              const fieldId = `bulk-field-${f.name}`
              return (
                <motion.div
                  key={f.name}
                  initial={{ opacity: 0, y: -4 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.18, ease: easeOutExpo }}
                  className="space-y-1.5"
                >
                  <div className="flex items-center justify-between gap-2">
                    <Label htmlFor={fieldId} className="gap-1.5">
                      <span>{fieldLabel(column)}</span>
                      {column.foreign_key && (
                        <Badge variant="secondary" className="font-normal">
                          → {column.foreign_key.table}
                        </Badge>
                      )}
                    </Label>
                    <button
                      type="button"
                      onClick={() => removeField(f.name)}
                      aria-label={`Remove ${fieldLabel(column)}`}
                      className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-destructive"
                    >
                      <XIcon className="size-3.5" />
                    </button>
                  </div>
                  <FieldControl
                    id={fieldId}
                    column={column}
                    value={f.value}
                    onChange={(v) => setValue(f.name, v)}
                    schema={meta.schema}
                    table={meta.name}
                    invalid={!!error}
                  />
                  {error && <p className="text-xs text-destructive">{error}</p>}
                </motion.div>
              )
            })}
          </div>
        </div>
      </ScrollArea>

      {/* Footer */}
      <div className="flex items-center justify-end gap-2 border-t bg-muted/30 px-5 py-3">
        <Button type="button" variant="ghost" onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <SaveButton loading={saving} disabled={fields.length === 0}>
          Apply to {count} {noun}
        </SaveButton>
      </div>
    </form>
  )
}
