import type { Page, Route } from '@playwright/test'

// Bind the fixtures to the real API contract so a runtime contract test
// (mock-api.contract.test.ts) catches drift from frontend/src/types.ts.
import type { ColumnMeta, FieldType, TableMeta, TableSummary } from '../src/types'

/**
 * mock-api.ts — A tiny in-memory stand-in for the FastAPI backend.
 *
 * Mirrors the response shapes in frontend/src/types.ts for one schema (`dbo`)
 * with two related tables (Employee → Department) so the e2e tests can exercise
 * the FK combobox and number field without a live database.
 */

function col(name: string, field_type: FieldType, extra: Partial<ColumnMeta> = {}): ColumnMeta {
  return {
    name,
    field_type,
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
  }
}

// Seed factories return FRESH arrays per installMockApi call, so tests that
// mutate rows (create, bulk delete) don't leak state into one another.
function seedDepartments(): Record<string, unknown>[] {
  return [
    { DepartmentID: 1, DepartmentName: 'Engineering' },
    { DepartmentID: 2, DepartmentName: 'Research' },
    { DepartmentID: 3, DepartmentName: 'Operations' },
    { DepartmentID: 4, DepartmentName: 'Finance' },
    { DepartmentID: 5, DepartmentName: 'Marketing' },
    { DepartmentID: 6, DepartmentName: 'Sales' },
    { DepartmentID: 7, DepartmentName: 'Human Resources' },
    { DepartmentID: 8, DepartmentName: 'Legal' },
  ]
}

function seedEmployees(): Record<string, unknown>[] {
  return [
    { EmployeeID: 1, FullName: 'Ada Lovelace', DepartmentID: 2, Salary: 120000, Rating: 4.5, IsActive: true, Notes: 'Pioneer.', CreatedDate: '2024-01-01T10:00:00' },
    { EmployeeID: 2, FullName: 'Alan Turing', DepartmentID: 1, Salary: 135000, Rating: 4.9, IsActive: true, Notes: null, CreatedDate: '2024-02-01T10:00:00' },
    { EmployeeID: 3, FullName: 'Grace Hopper', DepartmentID: 3, Salary: 128000, Rating: 5, IsActive: false, Notes: null, CreatedDate: '2024-03-01T10:00:00' },
  ]
}

export const tableMeta: Record<string, TableMeta> = {
  Employee: {
    schema: 'dbo',
    name: 'Employee',
    primary_key: ['EmployeeID'],
    display_column: 'FullName',
    concurrency_token: null,
    columns: [
      col('EmployeeID', 'integer', { editable: false, is_primary_key: true }),
      col('FullName', 'text', { required: true, nullable: false, max_length: 100 }),
      col('DepartmentID', 'integer', { foreign_key: { schema: 'dbo', table: 'Department', column: 'DepartmentID' } }),
      col('Salary', 'integer'),
      col('Rating', 'number'),
      col('IsActive', 'boolean', { nullable: false }),
      col('Notes', 'text', { max_length: null }),
      col('CreatedDate', 'datetime', { editable: false, is_audit: true }),
    ],
  },
  Department: {
    schema: 'dbo',
    name: 'Department',
    primary_key: ['DepartmentID'],
    display_column: 'DepartmentName',
    concurrency_token: null,
    columns: [
      col('DepartmentID', 'integer', { editable: false, is_primary_key: true }),
      col('DepartmentName', 'text', { required: true, nullable: false, max_length: 100 }),
    ],
  },
}

const perms = { insert: true, update: true, delete: true }

export const tablesList: TableSummary[] = [
  { name: 'Department', display_column: 'DepartmentName', primary_key: ['DepartmentID'], permissions: perms },
  { name: 'Employee', display_column: 'FullName', primary_key: ['EmployeeID'], permissions: perms },
]

const primaryKeyOf: Record<string, string[]> = {
  Employee: ['EmployeeID'],
  Department: ['DepartmentID'],
}

type FilterSpec = { op: string; value: unknown }

/** Apply the operator filters the frontend sends (mirrors crud.py::_filter_clause). */
function matchesFilters(row: Record<string, unknown>, filters: Record<string, FilterSpec>): boolean {
  for (const [col, spec] of Object.entries(filters)) {
    const v = row[col]
    const { op, value } = spec
    const num = v as number
    const text = String(v ?? '').toLowerCase()
    const q = String(value ?? '').toLowerCase()
    const pass =
      op === 'isnull' ? v == null
      : op === 'notnull' ? v != null
      : op === 'eq' ? v === value
      : op === 'ne' ? v !== value
      : op === 'gt' ? num > (value as number)
      : op === 'gte' ? num >= (value as number)
      : op === 'lt' ? num < (value as number)
      : op === 'lte' ? num <= (value as number)
      : op === 'between' ? num >= (value as number[])[0] && num <= (value as number[])[1]
      : op === 'in' ? (value as unknown[]).includes(v)
      : op === 'contains' ? text.includes(q)
      : op === 'startswith' ? text.startsWith(q)
      : op === 'endswith' ? text.endsWith(q)
      : true
    if (!pass) return false
  }
  return true
}

export async function installMockApi(
  page: Page,
  opts: { expireRowsOnce?: boolean; conflictOnWrite?: boolean } = {},
) {
  // When simulating an expired session, the first row query returns 401 until
  // the app refreshes the EasyAuth session via /.auth/refresh.
  let sessionRefreshed = false

  // Fresh, per-test mutable data so create/delete in one test can't leak.
  const departments = seedDepartments()
  const rowsByTable: Record<string, Record<string, unknown>[]> = {
    Employee: seedEmployees(),
    Department: departments,
  }

  const handled = (path: string) =>
    path === '/me' || path.startsWith('/.auth/') || /^\/(meta|api|admin)(\/|$)/.test(path)

  await page.route(
    (url) => handled(new URL(url).pathname),
    async (route: Route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    const method = route.request().method()
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

    // EasyAuth session refresh — marks the (simulated) session valid again.
    if (path === '/.auth/refresh') {
      sessionRefreshed = true
      return json({})
    }

    // GET /me — EasyAuth identity
    if (path === '/me') return json({ name: 'ada@contoso.com', id: 'abc-123', authenticated: true })

    // GET /meta
    if (path === '/meta') return json({ database: 'DemoDB', schemas: ['dbo'] })

    // GET /meta/dbo
    if (path === '/meta/dbo') return json({ schema: 'dbo', tables: tablesList })

    // GET /meta/dbo/{table}/options/{column}
    let m = path.match(/^\/meta\/dbo\/(\w+)\/options\/(\w+)$/)
    if (m) {
      // Every FK in this fixture points at Department.
      return json(departments.map((d) => ({ value: d.DepartmentID, label: d.DepartmentName })))
    }

    // GET /meta/dbo/{table}
    m = path.match(/^\/meta\/dbo\/(\w+)$/)
    if (m && method === 'GET') {
      const meta = tableMeta[m[1]]
      if (!meta) return json({}, 404)
      // In conflict mode, advertise a rowversion token on Employee so the app
      // sends If-Match on writes (and we can reject a stale one below).
      if (opts.conflictOnWrite && m[1] === 'Employee') {
        return json({ ...(meta as Record<string, unknown>), concurrency_token: 'RowVersion' })
      }
      return json(meta)
    }

    // POST /api/dbo/{table}/query
    m = path.match(/^\/api\/dbo\/(\w+)\/query$/)
    if (m && method === 'POST') {
      // Simulate an expired token on the first query: 401 until refreshed.
      if (opts.expireRowsOnce && !sessionRefreshed) {
        return json({ code: 'UNAUTHENTICATED', message: 'Your session has expired.' }, 401)
      }
      const rows = rowsByTable[m[1]] ?? []
      const body = (route.request().postDataJSON() ?? {}) as {
        search?: string
        filters?: Record<string, FilterSpec>
        sort?: { column?: string; direction?: string }
      }
      const search = (body.search ?? '').toLowerCase()
      const filters = body.filters ?? {}
      let result = rows.filter(
        (r) =>
          (!search || JSON.stringify(r).toLowerCase().includes(search)) &&
          matchesFilters(r, filters),
      )
      const sortCol = body.sort?.column
      if (sortCol) {
        const dir = body.sort?.direction === 'desc' ? -1 : 1
        result = [...result].sort((a, b) => {
          const av = a[sortCol]
          const bv = b[sortCol]
          if (av == null && bv == null) return 0
          if (av == null) return -dir
          if (bv == null) return dir
          return (av < bv ? -1 : av > bv ? 1 : 0) * dir
        })
      }
      // In conflict mode, tag each row with a rowversion the app echoes as
      // If-Match — so the write below can detect and reject the stale token.
      const data = opts.conflictOnWrite
        ? result.map((r) => ({ ...r, RowVersion: '0000000000000001' }))
        : result
      return json({ data, total: result.length, page: 1, page_size: 50, pages: 1 })
    }

    // POST /api/dbo/{table}/bulk-delete
    m = path.match(/^\/api\/dbo\/(\w+)\/bulk-delete$/)
    if (m && method === 'POST') {
      const table = m[1]
      const rows = rowsByTable[table] ?? []
      const pk = primaryKeyOf[table] ?? []
      const body = (route.request().postDataJSON() ?? {}) as {
        ids?: unknown[][]
        all_matching?: boolean
        search?: string
        filters?: Record<string, FilterSpec>
      }
      let doomed: Record<string, unknown>[]
      if (body.all_matching) {
        const search = (body.search ?? '').toLowerCase()
        const filters = body.filters ?? {}
        doomed = rows.filter(
          (r) =>
            (!search || JSON.stringify(r).toLowerCase().includes(search)) &&
            matchesFilters(r, filters),
        )
      } else {
        const ids = body.ids ?? []
        doomed = rows.filter((r) =>
          ids.some((tuple) => pk.every((c, i) => String(r[c]) === String(tuple[i]))),
        )
      }
      rowsByTable[table] = rows.filter((r) => !doomed.includes(r))
      return json({ deleted: doomed.length })
    }

    // POST /api/dbo/{table}/bulk-update
    m = path.match(/^\/api\/dbo\/(\w+)\/bulk-update$/)
    if (m && method === 'POST') {
      const table = m[1]
      const rows = rowsByTable[table] ?? []
      const pk = primaryKeyOf[table] ?? []
      const body = (route.request().postDataJSON() ?? {}) as {
        ids?: unknown[][]
        all_matching?: boolean
        search?: string
        filters?: Record<string, FilterSpec>
        values?: Record<string, unknown>
      }
      const values = body.values ?? {}
      let targets: Record<string, unknown>[]
      if (body.all_matching) {
        const search = (body.search ?? '').toLowerCase()
        const filters = body.filters ?? {}
        targets = rows.filter(
          (r) =>
            (!search || JSON.stringify(r).toLowerCase().includes(search)) &&
            matchesFilters(r, filters),
        )
      } else {
        const ids = body.ids ?? []
        targets = rows.filter((r) =>
          ids.some((tuple) => pk.every((c, i) => String(r[c]) === String(tuple[i]))),
        )
      }
      for (const r of targets) Object.assign(r, values)
      return json({ updated: targets.length })
    }

    // POST /api/dbo/{table}/bulk-create
    m = path.match(/^\/api\/dbo\/(\w+)\/bulk-create$/)
    if (m && method === 'POST') {
      const table = m[1]
      const rows = rowsByTable[table] ?? []
      const pkCol = (primaryKeyOf[table] ?? ['id'])[0]
      const body = (route.request().postDataJSON() ?? {}) as { rows?: Record<string, unknown>[] }
      const incoming = body.rows ?? []
      let nextId = rows.reduce((max, r) => Math.max(max, Number(r[pkCol]) || 0), 0)
      for (const r of incoming) {
        nextId += 1
        rows.push({ [pkCol]: nextId, ...r })
      }
      rowsByTable[table] = rows
      return json({ created: incoming.length })
    }

    // POST /api/dbo/{table}  (create)
    m = path.match(/^\/api\/dbo\/(\w+)$/)
    if (m && method === 'POST') {
      const values = (route.request().postDataJSON() ?? {}) as Record<string, unknown>
      const created = { EmployeeID: 999, CreatedDate: '2026-06-27T12:00:00', ...values }
      rowsByTable[m[1]]?.push(created)
      return json(created)
    }

    // GET single row /api/dbo/{table}/{pk}
    m = path.match(/^\/api\/dbo\/(\w+)\/([^/]+)$/)
    if (m && method === 'GET') {
      const [, table, pk] = m
      const row = (rowsByTable[table] ?? []).find((r) => String(Object.values(r)[0]) === pk)
      return json(row ?? {}, row ? 200 : 404)
    }

    // DELETE single row /api/dbo/{table}/{pk}
    m = path.match(/^\/api\/dbo\/(\w+)\/([^/]+)$/)
    if (m && method === 'DELETE') {
      const [, table, pk] = m
      const rows = rowsByTable[table] ?? []
      const pkCols = primaryKeyOf[table] ?? []
      const values = decodeURIComponent(pk).split(',')
      const kept = rows.filter((r) => !pkCols.every((c, i) => String(r[c]) === values[i]))
      rowsByTable[table] = kept
      return json({ deleted: pk }, kept.length < rows.length ? 200 : 404)
    }

    // PATCH single row /api/dbo/{table}/{pk} (update — used by the edit form and
    // inline cell editing).
    m = path.match(/^\/api\/dbo\/(\w+)\/([^/]+)$/)
    if (m && method === 'PATCH') {
      const [, table, pk] = m
      const rows = rowsByTable[table] ?? []
      const pkCols = primaryKeyOf[table] ?? []
      const values = decodeURIComponent(pk).split(',')
      const row = rows.find((r) => pkCols.every((c, i) => String(r[c]) === values[i]))
      if (!row) return json({}, 404)
      // Conflict mode: a write that carries the (stale) rowversion token is
      // refused with 409, exactly as the backend does on a concurrent change.
      if (opts.conflictOnWrite && route.request().headers()['if-match']) {
        return json(
          {
            code: 'CONFLICT',
            message:
              'This record was changed by someone else since you loaded it. ' +
              'Reload to get the latest version, then reapply your change.',
          },
          409,
        )
      }
      Object.assign(row, route.request().postDataJSON() ?? {})
      return json(row)
    }

    return json({ code: 'NOT_FOUND', message: `not mocked: ${method} ${path}` }, 404)
  })
}
