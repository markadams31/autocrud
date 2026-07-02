# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

One FastAPI app that points at any Azure SQL database and exposes a full CRUD API — plus a
schema-driven React SPA served from the same origin — for every table it finds. No
hand-written models, no table-specific code anywhere: the app reflects the live schema at
startup (and on `POST /admin/refresh`), generates per-table Pydantic validation models, and
the frontend builds its grids/forms from the `/meta` endpoints at runtime. A new data-management
need is a database provisioning task, not a software project. Deployed one-instance-per-database
to Azure App Service; internal-use posture (see error handling below).

## Commands

Backend (from `backend/`, uses [uv](https://docs.astral.sh/uv/)):

```bash
uv sync --extra test                      # install deps + test/type tooling
uv run pytest tests/unit tests/api        # fast tiers, no database (~10s)
uv run pytest -m integration              # real SQL Server via Testcontainers (see below)
uv run pytest tests/unit/test_errors.py::test_status_codes   # single test
uv run ruff check app tests               # lint
uv run pyright                            # type gate (basic mode, app/ only)
```

The integration tier needs Docker and the image **pre-pulled** (the fixtures skip rather than
pull): `docker pull mcr.microsoft.com/mssql/server:2025-latest`. It must be 2025 — the schema
exercises the native `json`/`vector` types. pyodbc + a local ODBC Driver 17/18 are needed only
to bootstrap the container (readiness poll + schema load); the app itself runs on mssql-python.

Frontend (from `frontend/`): `npm ci`, then `npm run dev` / `npm run build` (tsc + vite) /
`npm run test:unit` (vitest) / `npm run lint` (oxlint).

Docker image (build context is the **repo root**): `docker build -t autocrud .`

Pre-commit/pre-push hooks run ruff, bandit, pyright, the fast pytest tiers, oxlint, terraform
fmt, actionlint, and gitleaks — a commit that "does nothing" usually means a hook failed above
the fold; read the full hook output.

## Architecture

### Two identities, strictly separated (`app/connection.py`)

- **Managed identity** — used *only* for schema reflection and the `/health` ping. Its sole
  database grant is `VIEW DEFINITION` (validated: full reflection parity with sysadmin, zero
  data access). Never used for data.
- **The signed-in user** — used for *all* data access. App Service EasyAuth validates the user
  and injects an Azure SQL OBO token header; `get_user_db` builds a small pooled engine per
  token (cached by token string), and SQL Server enforces that user's own grants on every
  statement. **The app never checks permissions itself** — a denied grant surfaces as the
  database's error, mapped to 403. Local dev has no EasyAuth; browse through
  `backend/dev_auth_proxy.py`, which injects the same headers from your `az login` session.

### Reflection (`app/reflection.py`) — the core of the app

`reflect_schemas()` returns an immutable `SchemaSnapshot`, atomically swapped into
`app/state.py` (startup + `/admin/refresh`); in-flight requests finish on the old snapshot.
Facts come from exactly two sources with a hard boundary, one source per fact:

- **SQLAlchemy reflection** supplies what it reads reliably at any privilege: types, nullability,
  PKs, identity, MS_Description comments.
- **One catalog pass** (`_catalog_facts` → `ColumnFacts` per column) supplies what SQLAlchemy
  can't see reliably: computed/generated-always flags, default existence + (VIEW-DEFINITION-gated)
  default text, and FKs. Never fall back to SQLAlchemy's gated attributes (`col.computed`,
  `col.server_default`) — they are empty under least privilege and were deliberately removed
  from classification.

Columns classify as `EDITABLE` / `DB_OWNED` (identity, computed, value-generating default,
or a name in `DB_AUDIT_COLUMNS`) / `EXCLUDED` (type not writable). **Read/write policy is
decided here, once**: `ColumnInfo.searchable/filterable/read_as_text` — routes never
re-inspect SQLAlchemy types at request time. `read_as_text` columns (CLR types, sql_variant)
are CAST to NVARCHAR on read because the driver returns raw CLR bytes.

`app/mssql_types.py` registers SQL Server 2025 `json`/`vector` and the CLR types into the
mssql dialect's reflection map **as an import side effect** — reflection imports from it, so
the registrations precede the first `metadata.reflect()`. `json` maps to `str` (not dict) on
purpose: top-level JSON arrays/scalars must round-trip.

### Error contract (`app/errors.py`)

The machine-readable `code` + HTTP status is frontend API contract (session refresh on 401,
no-access state on 403, reload-on-conflict on 409, per-field form highlighting on 422). The
`message` for database errors is the **database's own text, verbatim** — a deliberate
internal-tool decision: no parsing/prettifying layer to drift out of date. Mapping uses the
DB-API exception class plus two text patterns; mssql-python exposes **no native SQL error
number anywhere**, so never rely on error numbers.

### CRUD routes (`app/routes/crud.py`)

Each request runs in one transaction (commit on return, rollback on raise) — bulk operations
are all-or-nothing by construction. Write payloads are scrubbed twice (generated Pydantic
model strips unknown/server-owned fields; a second pass removes non-editable columns).
Validation philosophy: the API pre-validates only `max_length`; everything semantic (CHECK
rules, FK existence, precision) is left to the database, which is the source of truth. Tables
with a rowversion column *require* `If-Match` on update/delete (409 on mismatch); tables
without one are last-writer-wins. Reflection disables `implicit_returning` per table because
SQL Server rejects OUTPUT on trigger-carrying tables.

### Frontend (`frontend/src`)

`types.ts` mirrors the backend JSON contract exactly (snake_case, no remapping). Everything
is metadata-driven off `/meta`. Convention for optional capability fields (`searchable`,
`filterable`, `description`): **absent means capable** — guard with `!== false`.

### Dependency and driver stack

mssql-python (bundles its own SQL Server driver — no ODBC install in the runtime image;
tokens pass via `attrs_before` {1256: packed}) on SQLAlchemy pinned to the 2.1 line (first
with the `mssql+mssqlpython` dialect). The Docker image installs dependencies **from
`uv.lock`** (`uv export --frozen` + `--require-hashes`), so dependency changes go
lockfile-first: editing `pyproject.toml` alone changes nothing until `uv lock` runs.

## Testing model

Three tiers plus a golden pin:

- **unit / api** — no database. The api tier runs routes against in-memory sqlite using
  hand-built tables; `tests/conftest.py`'s `make_table_info(..., schema="dbo", facts=...)`
  keys the snapshot under a schema while the table itself stays schemaless so SQL runs on
  sqlite, and `pk_default_facts()` marks the PK database-supplied. Catalog facts are always
  constructed explicitly (`ColumnFacts`) — hand-built SQLAlchemy markers are not consulted.
- **integration** — the real contract. The reflection matrix runs every scenario **twice**:
  as `sa` and as a login holding only `VIEW DEFINITION` (no data access); identical results
  are the module's core promise. `golden_snapshot.json` freezes the entire reflection output
  field-for-field. If you intentionally change reflection behaviour, regenerate with
  `UPDATE_GOLDEN=1 uv run pytest tests/integration/test_reflection_golden.py` and explain the
  diff in the PR — an unexplained golden diff is a bug.
- Hard-won rule: **reflection- or driver-dependent behaviour needs a real-database test.**
  Unit tests against hand-built tables have given false confidence twice (CHECK constraints,
  reflected `autoincrement` semantics).

## Conventions

- Commit style: `type(scope): summary` (`feat(backend):`, `refactor(infra):`, `test(backend):`).
- Branch from current `main`; PRs to `main`. Merging to `main` auto-deploys the dev environment.
- Infrastructure lives in `infra/` (Terraform, module + per-environment dirs); database-side
  design guidance (audit triggers, temporal tables, permissions) lives in `database/`.
