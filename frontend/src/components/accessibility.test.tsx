/**
 * accessibility.test.tsx — Automated WCAG 2.2 A/AA checks for the app's key
 * screens, plus targeted assertions for the accessibility affordances this
 * codebase relies on (skip link, keyboard-reachable row detail, programmatic
 * error association, expandable-region state).
 *
 * axe-core provides the structural sweep (see src/test/axe.ts for its jsdom
 * limits); the explicit assertions pin the specific behaviours axe under jsdom
 * can't fully judge but that matter for keyboard and screen-reader users.
 */

import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import { assertNoAxeViolations } from '@/test/axe'
import { AppShell } from '@/components/app-shell'
import { BulkActionBar } from '@/components/bulk-action-bar'
import { DataTable } from '@/components/data-table'
import { EmptyState } from '@/components/states'
import { PaginationBar } from '@/components/pagination-bar'
import { RecordForm } from '@/components/record-form'
import { TableToolbar } from '@/components/table-toolbar'
import { ThemeProvider } from '@/components/theme-provider'
import { TooltipProvider } from '@/components/ui/tooltip'
import type { FkLabelMap } from '@/hooks/queries'
import type { ColumnMeta, Row, TableMeta, TablePermissions } from '@/types'

// jsdom has no matchMedia; ThemeProvider (and Motion's reduced-motion) read it.
beforeAll(() => {
  if (!window.matchMedia) {
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia
  }
})

// ── Fixtures ─────────────────────────────────────────────────────────────────

const col = (name: string, overrides: Partial<ColumnMeta> = {}): ColumnMeta => ({
  name,
  field_type: 'text',
  sql_type: 'nvarchar(100)',
  nullable: true,
  required: false,
  editable: true,
  is_primary_key: false,
  is_audit: false,
  max_length: 100,
  precision: null,
  scale: null,
  foreign_key: null,
  ...overrides,
})

const COLUMNS: ColumnMeta[] = [
  col('Id', { field_type: 'integer', editable: false, is_primary_key: true, required: false }),
  col('Title', { required: true }),
  col('Active', { field_type: 'boolean' }),
]

const ROWS: Row[] = [
  { Id: 1, Title: 'Alpha', Active: true },
  { Id: 2, Title: 'Beta', Active: false },
]

const ALL_PERMS: TablePermissions = { insert: true, update: true, delete: true }
const NO_FK_LABELS: FkLabelMap = {} as FkLabelMap

const META: TableMeta = {
  schema: 'dbo',
  name: 'Widget',
  primary_key: ['Id'],
  display_column: 'Title',
  concurrency_token: null,
  columns: COLUMNS,
}

function withQueryClient(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>
}

// ── axe sweeps ───────────────────────────────────────────────────────────────

describe('axe — no WCAG A/AA violations', () => {
  it('data grid (with selection, view/edit/delete actions)', async () => {
    const { container } = render(
      <TooltipProvider>
        <DataTable
          columns={COLUMNS}
          rows={ROWS}
          fkLabels={NO_FK_LABELS}
          sort={{ column: '', direction: 'asc' }}
          onSort={() => {}}
          loading={false}
          permissions={ALL_PERMS}
          onRowClick={() => {}}
          onEdit={() => {}}
          onDelete={() => {}}
          schema="dbo"
          table="Widget"
          selection={{ isSelected: () => false, onToggleRow: () => {}, onToggleAllLoaded: () => {} }}
        />
      </TooltipProvider>,
    )
    await assertNoAxeViolations(container)
  })

  it('record create form', async () => {
    const { container } = render(
      withQueryClient(<RecordForm meta={META} mode="create" onClose={() => {}} onSaved={() => {}} />),
    )
    await assertNoAxeViolations(container)
  })

  it('bulk action bar', async () => {
    const { container } = render(
      <BulkActionBar
        count={2}
        allMatching={false}
        total={2}
        canSelectAllMatching={false}
        onSelectAllMatching={() => {}}
        onClear={() => {}}
        onEdit={() => {}}
        onDelete={() => {}}
      />,
    )
    await assertNoAxeViolations(container)
  })

  it('pagination bar', async () => {
    const { container } = render(
      <PaginationBar
        page={1}
        pages={3}
        total={120}
        pageSize={50}
        pageSizeOptions={[25, 50, 100]}
        onPageChange={() => {}}
        onPageSizeChange={() => {}}
      />,
    )
    await assertNoAxeViolations(container)
  })

  it('table toolbar', async () => {
    const { container } = render(
      <TableToolbar search="" onSearchChange={() => {}} canInsert onNew={() => {}} />,
    )
    await assertNoAxeViolations(container)
  })

  it('empty state', async () => {
    const { container } = render(<EmptyState title="No records" description="Nothing here yet." />)
    await assertNoAxeViolations(container)
  })
})

// ── Targeted behaviour ───────────────────────────────────────────────────────

describe('keyboard-reachable row detail', () => {
  it('every row exposes a View control (not just a mouse-only row click)', () => {
    render(
      <TooltipProvider>
        <DataTable
          columns={COLUMNS}
          rows={ROWS}
          fkLabels={NO_FK_LABELS}
          sort={{ column: '', direction: 'asc' }}
          onSort={() => {}}
          loading={false}
          permissions={{ insert: false, update: false, delete: false }}
          onRowClick={() => {}}
          onEdit={() => {}}
          onDelete={() => {}}
          schema="dbo"
          table="Widget"
        />
      </TooltipProvider>,
    )
    // Read-only table (no update/delete) still offers a keyboard-focusable View
    // button per row — the read-only detail isn't mouse-only.
    expect(screen.getAllByRole('button', { name: 'View details' })).toHaveLength(ROWS.length)
  })
})

describe('form error is programmatically associated with its field', () => {
  it('sets aria-invalid and aria-describedby pointing at the visible error', () => {
    const { container } = render(
      withQueryClient(<RecordForm meta={META} mode="create" onClose={() => {}} onSaved={() => {}} />),
    )

    // Submit with the required Title empty → an inline error is shown.
    fireEvent.click(screen.getByRole('button', { name: 'Create record' }))

    const input = container.querySelector('#field-Title')!
    expect(input).toHaveAttribute('aria-invalid', 'true')
    const describedBy = input.getAttribute('aria-describedby')
    expect(describedBy).toBe('field-Title-error')

    const error = document.getElementById(describedBy!)
    expect(error).not.toBeNull()
    expect(error).toHaveTextContent('This field is required.')
  })
})

describe('app shell landmarks and skip link', () => {
  it('renders a skip link, a main landmark, and passes an axe page sweep', async () => {
    // Match index.html so page-level rules (lang, title) reflect the real app.
    document.documentElement.lang = 'en'
    document.title = 'Auto CRUD'
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } })),
    )

    render(
      withQueryClient(
        <ThemeProvider>
          <TooltipProvider>
            <AppShell />
          </TooltipProvider>
        </ThemeProvider>,
      ),
    )

    // Sidebar brand text is always present — a stable anchor that the shell mounted.
    await screen.findByText('Schema explorer')

    const skip = screen.getByRole('link', { name: /skip to main content/i })
    expect(skip).toHaveAttribute('href', '#main-content')
    const main = screen.getByRole('main')
    expect(main).toHaveAttribute('id', 'main-content')

    await assertNoAxeViolations(document.documentElement)

    vi.unstubAllGlobals()
  })
})
