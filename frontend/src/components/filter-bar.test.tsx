import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { FilterBar } from '@/components/filter-bar'
import type { ColumnMeta } from '@/types'

const col = (name: string, extra: Partial<ColumnMeta> = {}): ColumnMeta => ({
  name,
  field_type: 'text',
  nullable: true,
  required: false,
  editable: true,
  is_primary_key: false,
  is_audit: false,
  max_length: null,
  precision: null,
  scale: null,
  foreign_key: null,
  ...extra,
})

describe('FilterBar — "Add filter" candidates follow ColumnMeta.filterable', () => {
  it('omits columns reflection marked non-filterable', async () => {
    const user = userEvent.setup()
    render(
      <FilterBar
        columns={[
          col('Name'),
          col('Payload', { filterable: false }), // e.g. xml/varbinary/vector
          col('Score', { filterable: true, field_type: 'integer' }),
        ]}
        filters={{}}
        onChange={() => {}}
        schema="dbo"
        table="T"
      />,
    )
    await user.click(screen.getByRole('button', { name: /add filter/i }))
    expect(await screen.findByRole('menuitem', { name: 'Name' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Score' })).toBeInTheDocument()
    // The server refuses value filters on this column type, so it is never offered.
    expect(screen.queryByRole('menuitem', { name: 'Payload' })).not.toBeInTheDocument()
  })

  it('treats columns without the flag as filterable (older payloads, fixtures)', async () => {
    const user = userEvent.setup()
    render(
      <FilterBar
        columns={[col('Legacy')]} // no `filterable` key at all
        filters={{}}
        onChange={() => {}}
        schema="dbo"
        table="T"
      />,
    )
    await user.click(screen.getByRole('button', { name: /add filter/i }))
    expect(await screen.findByRole('menuitem', { name: 'Legacy' })).toBeInTheDocument()
  })
})
