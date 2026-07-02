/**
 * types.ts — TypeScript mirror of the backend API contract.
 *
 * Every shape here corresponds to a JSON payload produced by the FastAPI
 * routes in backend/app/routes. Field names match the Python responses
 * exactly (snake_case) so no remapping layer is needed.
 */

// ── Column field types ───────────────────────────────────────────────────────
// Produced by meta.py::_field_type. The frontend switches on these to choose
// which input/renderer to use. `decimal` arrives as a JSON string to preserve
// precision (see the backend _AppEncoder), so it is treated as text on input.
export type FieldType =
  | 'text'
  | 'integer'
  | 'number'
  | 'decimal'
  | 'boolean'
  | 'date'
  | 'datetime'
  | 'time'

// ── Metadata shapes (GET /meta/...) ──────────────────────────────────────────

export interface ForeignKeyRef {
  schema: string
  table: string
  column: string
}

/** One column, from meta.py::_column_response. */
export interface ColumnMeta {
  name: string
  field_type: FieldType
  /**
   * Precise SQL Server type for display, e.g. "NVARCHAR(255)" or "DECIMAL(18, 2)".
   * Always present from the backend; optional only so lightweight test fixtures
   * can omit it (the header card falls back to field_type).
   */
  sql_type?: string
  nullable: boolean
  /** Client MUST supply this on create (NOT NULL, no default, not auto-PK). */
  required: boolean
  /** Client-writable. DB-owned/excluded columns are read-only. */
  editable: boolean
  is_primary_key: boolean
  /** Named in DB_AUDIT_COLUMNS — populated by the database, shown muted. */
  is_audit: boolean
  /**
   * Column-level capabilities decided at reflection time. Optional so
   * lightweight test fixtures can omit them; treat absence as capable.
   */
  /** Free-text search (LIKE) is valid against this column. */
  searchable?: boolean
  /** Value filters (=, range, in, contains…) are valid against this column. */
  filterable?: boolean
  /** MS_Description authored in the database, or null — render as help text. */
  description?: string | null
  max_length: number | null
  precision: number | null
  scale: number | null
  foreign_key: ForeignKeyRef | null
}

/** Full table description, from meta.py::describe_table (GET /meta/{schema}/{table}). */
export interface TableMeta {
  schema: string
  name: string
  primary_key: string[]
  display_column: string | null
  /**
   * Name of the rowversion column, or null. When set, its value is present on
   * every row and echoed back as If-Match on update/delete for optimistic
   * concurrency (a stale write gets a CONFLICT). Null → last-writer-wins.
   */
  concurrency_token: string | null
  columns: ColumnMeta[]
}

/** Per-table permission flags resolved against the signed-in user. */
export interface TablePermissions {
  insert: boolean
  update: boolean
  delete: boolean
}

/** One row of the sidebar table list, from meta.py::list_tables. */
export interface TableSummary {
  name: string
  display_column: string | null
  primary_key: string[]
  permissions: TablePermissions
}

/** GET /meta/{schema} */
export interface SchemaTables {
  schema: string
  tables: TableSummary[]
}

/** GET /meta */
export interface SchemaList {
  /** Name of the connected database — shown in the sidebar header. */
  database: string
  schemas: string[]
}

/** GET /config — runtime settings the browser bootstraps from (admin.py). */
export interface AppConfig {
  applicationInsights: {
    /** App Insights connection string, or null when telemetry isn't configured. */
    connectionString: string | null
  }
}

/** GET /version — the running backend build (app/build_info.py). */
export interface BuildInfo {
  /** Commit SHA the image was built from; "dev" outside a CI-built image. */
  sha: string
  /** UTC build timestamp (ISO 8601); "dev" outside a CI-built image. */
  time: string
}

/** A FK dropdown option, from meta.py::get_options. */
export interface FieldOption {
  value: string | number
  label: string
}

/** GET /me — the EasyAuth-authenticated identity (routes/identity.py). */
export interface Me {
  name: string | null
  id: string | null
  authenticated: boolean
}

// ── Data shapes (POST/GET /api/...) ──────────────────────────────────────────

/** A single data row. Values are whatever the column's JSON encoding produces. */
export type Row = Record<string, unknown>

export type SortDirection = 'asc' | 'desc'

/**
 * Filter operators understood by the backend (crud.py::_filter_clause).
 * `isnull`/`notnull` take no value; `between` takes a [low, high] pair;
 * everything else takes a single value.
 */
export type FilterOp =
  | 'eq'
  | 'ne'
  | 'contains'
  | 'startswith'
  | 'endswith'
  | 'gt'
  | 'gte'
  | 'lt'
  | 'lte'
  | 'between'
  | 'in'
  | 'isnull'
  | 'notnull'

/** One column filter sent to the API: an operator plus its (coerced) value. */
export interface FilterSpec {
  op: FilterOp
  value: unknown
}

/** POST /api/{schema}/{table}/query request body, from crud.py::QueryRequest. */
export interface QueryRequest {
  search: string
  filters: Record<string, FilterSpec>
  sort: { column: string; direction: SortDirection }
  page: number
  page_size: number
}

/** POST /api/{schema}/{table}/query response, from crud.py::query_rows. */
export interface QueryResponse {
  data: Row[]
  total: number
  page: number
  page_size: number
  pages: number
}

/**
 * POST /api/{schema}/{table}/bulk-delete request, from crud.py::BulkDeleteRequest.
 * Two modes: an explicit list of primary keys (`ids`, each an array of PK values
 * in primary-key order), or `all_matching` with the same search/filters the grid
 * used. The whole batch is deleted atomically (one transaction).
 */
export interface BulkDeleteRequest {
  ids?: unknown[][]
  all_matching?: boolean
  search?: string
  filters?: Record<string, FilterSpec>
}

/** POST /api/{schema}/{table}/bulk-delete response. */
export interface BulkDeleteResponse {
  deleted: number
}

/**
 * POST /api/{schema}/{table}/bulk-update request, from crud.py::BulkUpdateRequest.
 * Targets rows the same two ways as bulk-delete (an explicit `ids` list, or
 * `all_matching` with the grid's search/filters); `values` is the column→new-value
 * map applied to every targeted row. Only the listed columns are written — all
 * else is left untouched — and the whole batch is updated atomically.
 */
export interface BulkUpdateRequest {
  ids?: unknown[][]
  all_matching?: boolean
  search?: string
  filters?: Record<string, FilterSpec>
  values: Row
}

/** POST /api/{schema}/{table}/bulk-update response. */
export interface BulkUpdateResponse {
  updated: number
}

/**
 * POST /api/{schema}/{table}/bulk-create request, from crud.py::BulkCreateRequest.
 * `rows` is a list of column→value maps (typically a parsed CSV). Each row is
 * validated and scrubbed like a single insert, and the whole import is atomic.
 */
export interface BulkCreateRequest {
  rows: Row[]
}

/** POST /api/{schema}/{table}/bulk-create response. */
export interface BulkCreateResponse {
  created: number
}

// ── Error contract (errors.py) ───────────────────────────────────────────────

export type ErrorCode =
  | 'NOT_FOUND'
  | 'VALIDATION_ERROR'
  | 'CONSTRAINT_VIOLATION'
  | 'CONFLICT'
  | 'BAD_REQUEST'
  | 'UNAUTHENTICATED'
  | 'PERMISSION_DENIED'
  | 'DATABASE_UNAVAILABLE'
  | 'INTERNAL_ERROR'

/** The single error JSON shape every non-2xx response uses (errors.py). */
export interface ApiErrorBody {
  code: ErrorCode
  message: string
  /** Per-field validation detail, present only for VALIDATION_ERROR. */
  fields?: Record<string, string>
  /** Bulk import: 0-based index of the row a database constraint rejected. */
  row?: number
  /** Bulk import: per-row field errors ({ "<index>": { "<col>": "<msg>" } }). */
  rows?: Record<string, Record<string, string>>
}
