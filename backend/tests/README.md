# Backend tests

```powershell
cd backend
uv sync --extra test        # installs the app + test dependencies
uv run pytest               # tiers 1 & 2 run; tier 3 auto-skips without Docker
```

Three tiers, fastest first:

| Tier | Path | Needs | What it covers |
|---|---|---|---|
| 1 — unit | `tests/unit/` | nothing | Reflection classification, type mapping, required/PK logic, display-column heuristic, the Pydantic model factory, the error contract, token/cache plumbing, metadata serialisation, config parsing. Built against **mssql dialect types** so behaviour matches production. |
| 2 — API | `tests/api/` | nothing | Routes via FastAPI `TestClient` over in-memory SQLite with dependencies overridden: payload scrubbing, validation, search/filter/sort/paging, partial updates, auth (401), the error JSON shape. |
| 3 — integration | `tests/integration/` | **Docker** | The whole reflection matrix against **real SQL Server** (Testcontainers), plus CRUD round-trips proving `SCOPE_IDENTITY` inserts on trigger tables, computed columns, value-generating defaults, and audit triggers. |

## Running just one tier

```powershell
uv run pytest tests/unit tests/api      # no Docker
uv run pytest -m "not integration"      # same, by marker
uv run pytest tests/integration         # Docker only
```

## Tier 3 (Docker)

Pull the SQL Server image once, then run:

```powershell
docker pull mcr.microsoft.com/mssql/server:2022-latest
uv run pytest tests/integration
```

The tier **auto-skips** (never hangs or fails) when Docker isn't running or the
image isn't present locally — so the default `pytest` run is always safe. It
needs a local `ODBC Driver 18/17 for SQL Server` (the same driver the app uses).

`tests/integration/schema.sql` is the comprehensive fixture: every column type,
identity / manual / composite / no PK, computed columns, value-generating vs
plain defaults, trigger audit columns, single / dual / self-referential /
cross-schema foreign keys, and a temporal (system-versioned) table.
