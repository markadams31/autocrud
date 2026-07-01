# Designing a database for Auto CRUD

*How the app reads your schema — and how to model a table so it keeps a trustworthy audit
trail and isn't destroyed by accident, using database features alone, with no changes to the
application.*

This folder holds the example schema (`seed.sql`) and access setup (`permissions.sql`). This
README is the design guide above them, because of one architectural fact:

> **The application adds no logic of its own.** It reflects whatever schema it finds and
> authorises every operation with SQL grants. So behaviour like "who changed this row" or
> "users may amend but not destroy records" is a property of **how the database is modelled
> and granted**, not of the app. Model the database for it and the app follows for free.

It's written to be usable by a data modeller designing a schema for this app. Part 1 
is the reflection contract (what the app reacts to). Part 2 maps common records
and audit obligations to schema features. Part 3 is a copy-pasteable pattern. Part 4 
is a checklist. Part 5 is an honest list of what the app does *not* do.

### A note on proportionality

Not every table needs any of Part 2 onward. Audit and retention obligations apply to
**records** — data with ongoing business, legal, or accountability value. They do **not**
apply to transitory or low-value data (drafts, working scratch, duplicates) or to
reference/lookup tables, which most regimes let you delete routinely. Apply the patterns
below only to the tables that genuinely hold records; leave everything else as plain tables
with ordinary deletes. Scale the controls to the value and sensitivity of the data.

Obligations of this kind appear across regulated and public-sector systems — public-records
legislation, financial and audit regimes, privacy law, and industry regulation. The specifics
vary by jurisdiction and sector; the schema patterns here are a general way to satisfy the
common core (a tamper-evident audit trail, and controlled rather than ad-hoc destruction).

---

## Part 1 — How the app reads your schema (the reflection contract)

The app reflects every table in the configured schemas (`DB_SCHEMAS`) on startup and on
`POST /admin/refresh`, and builds its API and UI from what it finds. There is **no per-table
configuration**. The full reference is in the [root README](../README.md); the rules that
matter for the patterns below are these.

### Column classification — who may write what

Every column is sorted into one of three kinds. This is the mechanism you use to make a
column **database-owned** (never writable by a user through the app):

| Kind | Meaning | How a column lands here |
|---|---|---|
| **Editable** | Client may read and write it | anything not caught below |
| **DB-owned** | Read-only to clients; the database sets it; writes are scrubbed | `IDENTITY`; computed `AS` columns; `GENERATED ALWAYS` period columns; a **value-generating default** (`SYSUTCDATETIME()`, `NEWID()`, …); **or a column named in `DB_AUDIT_COLUMNS`** |
| **Excluded** | Not writable through a generic layer | `rowversion`/binary, `XML`, `sql_variant` |

Two things you'll rely on:

- **`DB_AUDIT_COLUMNS` protects trigger-populated columns.** SQL Server exposes no metadata
  linking a trigger to the columns it writes, so the app can't detect them structurally. List
  them by name (case-insensitive, comma-separated) in the `DB_AUDIT_COLUMNS` env var and the
  app treats them as DB-owned — reflected, shown read-only, and **stripped from every write**.
  The example deployment uses `CreatedBy,CreatedDate,ModifiedBy,ModifiedDate`.
- **`SUSER_SNAME()` is the real signed-in user.** All data connections run as the signed-in
  user (OBO), so `SUSER_SNAME()` in a `DEFAULT` or an `AFTER` trigger resolves to the actual
  caller — not the application identity. This is what makes database-side audit attribution
  trustworthy, and it works for *every* client that touches the data, not just this API.

### Other reactions

- **A primary key is mandatory** — a table without one is skipped entirely (can't be addressed
  row-by-row).
- **`rowversion` opts a table into optimistic concurrency** — the app requires `If-Match` on
  update/delete and returns `409` on a conflicting write.
- **Temporal history tables are hidden** — a system-versioned table's history side
  (`temporal_type = 1`) is excluded from the API; the current table is served normally.
- **Foreign keys become dropdowns**, labelled by the referenced table's display column (a
  non-PK column whose name contains `name > label > title > description > code`, else the
  first text column).

---

## Part 2 — Obligation → schema mechanism

| Obligation | What it needs | Schema mechanism |
|---|---|---|
| **Trustworthy audit trail** | Who changed what, when, with before/after — tamper-evident | **System-versioned temporal** table (portable; history is unwritable while versioning is on) — or **ledger** tables for cryptographic proof — plus an actor trigger |
| **Attribution** | Every change tied to a real person | `AFTER INSERT, UPDATE` trigger writing `SUSER_SNAME()` into audit columns (named in `DB_AUDIT_COLUMNS`) |
| **No accidental/ad-hoc destruction** | Records amended, not deleted, by ordinary users | **Withhold the `DELETE` grant**; retire records with an editable status flag (a normal, audited `UPDATE`) instead |
| **Least-privilege access** | Only the right people read/write | SQL grants per schema/table — the app enforces none of its own |

Note that a *trustworthy audit trail* and *no ad-hoc destruction* are two separate controls:
temporal/ledger gives you the audit (and preserves deleted-row content in history);
withholding `DELETE` plus a retire flag gives you a findable, restorable "retired" state
instead of destruction. A records table usually wants both.

---

## Part 3 — The pattern (copy-paste)

A single table that satisfies Part 2 using only features the app already reacts to. Adapt the
columns to your domain; keep the structural elements.

```sql
-- ── An audited, retire-not-delete records table ──────────────────────────────
-- Immutable history (temporal) + real-user attribution (trigger) + retire-not-delete
-- (IsActive flag) + concurrency (rowversion). No DELETE grant is given to users
-- (see permissions, below), so a record can be retired but not destroyed through the app.
CREATE TABLE dbo.CaseRecord (
    CaseRecordID   INT            IDENTITY(1,1) NOT NULL,      -- IDENTITY → DB-owned
    CaseReference  NVARCHAR(30)   NOT NULL,                    -- business key
    Title          NVARCHAR(200)  NOT NULL,                    -- wins the display heuristic
    Summary        NVARCHAR(MAX)  NULL,

    -- Retire-not-delete marker. An ordinary editable column the app renders as a
    -- switch; unsetting it "retires" the record (a normal, fully-audited UPDATE).
    IsActive       BIT            NOT NULL CONSTRAINT DF_CaseRecord_Active DEFAULT 1,

    -- Audit + retire stamps. Trigger-populated, so list all of these in
    -- DB_AUDIT_COLUMNS to keep the client from ever writing them.
    DeletedBy      NVARCHAR(128)  NULL,
    DeletedDate    DATETIME2(7)   NULL,
    CreatedBy      NVARCHAR(128)  NULL,
    CreatedDate    DATETIME2(7)   NULL,
    ModifiedBy     NVARCHAR(128)  NULL,
    ModifiedDate   DATETIME2(7)   NULL,

    RowVersion     ROWVERSION     NOT NULL,                    -- opts into optimistic concurrency

    -- System-versioning period columns (GENERATED ALWAYS → DB-owned, excluded from writes).
    ValidFrom      DATETIME2(7)   GENERATED ALWAYS AS ROW START NOT NULL,
    ValidTo        DATETIME2(7)   GENERATED ALWAYS AS ROW END   NOT NULL,
    PERIOD FOR SYSTEM_TIME (ValidFrom, ValidTo),

    CONSTRAINT PK_CaseRecord  PRIMARY KEY (CaseRecordID),
    CONSTRAINT UQ_CaseRecord_Reference UNIQUE (CaseReference)
)
WITH (SYSTEM_VERSIONING = ON (HISTORY_TABLE = dbo.CaseRecordHistory));
GO

-- ── Actor + retire-stamp trigger ─────────────────────────────────────────────
-- SUSER_SNAME() resolves to the real signed-in user under the app's OBO connection.
-- Recursive triggers are off by default, so the trigger's own UPDATEs don't re-fire it.
-- On a temporal table these writes share one transaction, so they collapse into a
-- SINGLE history version (temporal versions at transaction granularity) — no churn.
CREATE TRIGGER dbo.trg_CaseRecord_Audit ON dbo.CaseRecord AFTER INSERT, UPDATE AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @now DATETIME2(7)  = SYSUTCDATETIME();
    DECLARE @who NVARCHAR(128) = SUSER_SNAME();

    -- Every write refreshes Modified*.
    UPDATE t SET t.ModifiedBy = @who, t.ModifiedDate = @now
    FROM dbo.CaseRecord t JOIN inserted i ON t.CaseRecordID = i.CaseRecordID;

    -- Created* on INSERT only (no rows in `deleted`).
    IF NOT EXISTS (SELECT 1 FROM deleted)
        UPDATE t SET t.CreatedBy = @who, t.CreatedDate = @now
        FROM dbo.CaseRecord t JOIN inserted i ON t.CaseRecordID = i.CaseRecordID;

    -- Stamp who/when when a record is retired (IsActive 1 → 0); clear on restore (0 → 1).
    UPDATE t SET t.DeletedBy = @who, t.DeletedDate = @now
    FROM dbo.CaseRecord t
    JOIN inserted i ON t.CaseRecordID = i.CaseRecordID
    JOIN deleted  d ON d.CaseRecordID = i.CaseRecordID
    WHERE i.IsActive = 0 AND d.IsActive = 1;

    UPDATE t SET t.DeletedBy = NULL, t.DeletedDate = NULL
    FROM dbo.CaseRecord t
    JOIN inserted i ON t.CaseRecordID = i.CaseRecordID
    JOIN deleted  d ON d.CaseRecordID = i.CaseRecordID
    WHERE i.IsActive = 1 AND d.IsActive = 0;
END;
GO
```

Add the trigger-populated columns to the app's config so a client can never write them:

```
DB_AUDIT_COLUMNS=CreatedBy,CreatedDate,ModifiedBy,ModifiedDate,DeletedBy,DeletedDate
```

### The grant that makes "retire, don't delete" real

The shipped `permissions.sql` grants `SELECT, INSERT, UPDATE, DELETE` for the demo. To make
records amendable but not destroyable, **omit `DELETE`** from the app-users grant:

```sql
-- App users: read, create, amend — but not destroy.
GRANT SELECT, INSERT, UPDATE ON SCHEMA::dbo TO [autocrud-<env>-users];
-- DELETE is deliberately NOT granted. With no DELETE grant the app hides the Delete control
-- and hard deletion is impossible for users; records are retired by unsetting IsActive.
```

If a record ever must be genuinely destroyed (e.g. once its retention has lapsed and
destruction is authorised), that's a deliberate, privileged action a DBA performs out of band
under whatever authority your retention policy defines — not something ordinary app users can
do.

> **Stronger substrate.** Swap `SYSTEM_VERSIONING` for updatable **ledger** tables
> (`LEDGER = ON`) to get cryptographic tamper-evidence — the engine records the committing
> principal, operation, and before/after natively (deletes included) and produces database
> digests you can publish to immutable storage for independent verification. The app reflects
> a ledger table's current side the same way, so no app change is needed; the trade-off is
> ledger's rigidity (tables are permanent, some operations restricted).

---

## Part 4 — Checklist (for the tables that hold records)

- [ ] **Primary key** present (or the app skips the table).
- [ ] **System-versioned temporal** (or updatable ledger) for immutable who/what/when/before-after.
- [ ] **Actor trigger** writing `SUSER_SNAME()` into `CreatedBy`/`ModifiedBy` (and retire
      stamps), with every such column listed in **`DB_AUDIT_COLUMNS`**.
- [ ] **Retire-not-delete** — an editable `IsActive`/status column, with the **`DELETE` grant
      withheld** from app users.
- [ ] `ROWVERSION` for optimistic concurrency (recommended).
- [ ] A descriptive **display column** (`Title`/`Name`/…) for readable labels and FK dropdowns.

Reference/lookup and transitory tables need none of this — leave them plain.

---

## Part 5 — What the app does *not* do (read this)

These patterns are carried by the **database**. The application is deliberately unchanged, so
it does **not**:

- **Show the audit history in the UI.** Temporal/ledger history is captured but the app hides
  it — read it directly (`SELECT ... FROM dbo.CaseRecord FOR SYSTEM_TIME ALL`, or the ledger
  view) or through a reporting tool.
- **Hide retired rows.** A record with `IsActive = 0` still appears in the grid; users filter
  `IsActive = true` themselves. A pre-filtering view won't help — the app reflects tables, not
  views.
- **Enforce the pattern.** Nothing stops a modeller creating a non-temporal, `DELETE`-grantable
  table; consistency across tables is a review responsibility, not something the app checks.

These are usability gaps, not gaps in what the database captures. Surfacing history, hiding
retired rows, or reporting non-conforming tables would be additive app features — not required
for the database to behave as designed.

---

## See also

- [`seed.sql`](seed.sql) — the worked example schema; note `dbo.LaborRate` (temporal) and the
  `SUSER_SNAME()` audit triggers — the building blocks combined above.
- [`permissions.sql`](permissions.sql) — the contained-user + schema-grant setup to adapt for
  the withhold-`DELETE` pattern.
- [root README](../README.md) — the full reflection model, column classification, and env vars.
