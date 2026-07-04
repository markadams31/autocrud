-- ============================================================================
-- Comprehensive reflection fixture schema.
--
-- Exercises every situation reflection must handle, against real SQL Server:
--   - every supported column type (numeric, money, float, all date/time, all
--     string, uniqueidentifier)
--   - every excluded-for-write type (binary, image, xml, sql_variant, rowversion)
--   - hierarchyid (unknown type → str / EDITABLE fallback)
--   - identity PK, manual single PK, composite PK, no PK (skipped)
--   - computed (persisted and non-persisted)
--   - value-generating defaults (sysutcdatetime / newid) vs a plain default
--   - trigger-populated audit columns
--   - FKs: single, two to the same table, self-referential, nullable, cross-schema
--   - temporal system-versioning (history table excluded; GENERATED ALWAYS cols)
--   - multiple schemas (dbo + app2)
--   - a schema OUTSIDE the configured set, FK-referenced from dbo (must never
--     be pulled into the snapshot)
--   - legacy text/ntext (whose reflected "length" is the 16-byte LOB pointer,
--     not a real limit), an alias UDT, and a sequence-fed default
--   - a least-privilege login (VIEW DEFINITION only, no data access) that the
--     reflection matrix re-runs under — see conftest.vdonly_engine
-- Loaded into a fresh database, so no DROP statements are needed.
-- ============================================================================

CREATE SCHEMA app2;
GO

-- ── Reference table — name beats code heuristic, FK target ───────────────────
CREATE TABLE dbo.Category (
    CategoryID   INT           IDENTITY(1,1) NOT NULL CONSTRAINT PK_Category PRIMARY KEY,
    CategoryCode NVARCHAR(10)  NOT NULL,
    CategoryName NVARCHAR(100) NOT NULL
);
GO

-- ── Manual single-column (non-identity) primary key ──────────────────────────
CREATE TABLE dbo.ManualKey (
    Code  NVARCHAR(10)  NOT NULL CONSTRAINT PK_ManualKey PRIMARY KEY,
    Label NVARCHAR(100) NOT NULL
);
GO

-- ── Composite primary key ────────────────────────────────────────────────────
CREATE TABLE dbo.Composite (
    OrgID INT          NOT NULL,
    TagID INT          NOT NULL,
    Note  NVARCHAR(100) NULL,
    CONSTRAINT PK_Composite PRIMARY KEY (OrgID, TagID)
);
GO

-- ── Heap with no primary key — must be skipped by reflection ──────────────────
CREATE TABLE dbo.NoPk (
    Value NVARCHAR(100) NULL
);
GO

-- ── FK target referenced twice from one table ────────────────────────────────
CREATE TABLE dbo.Employee (
    EmployeeID INT           IDENTITY(1,1) NOT NULL CONSTRAINT PK_Employee PRIMARY KEY,
    FullName   NVARCHAR(100) NOT NULL
);
GO

-- ── Two FKs to the same table + a cross-table FK + computed persisted col ─────
CREATE TABLE dbo.Project (
    ProjectID    INT           IDENTITY(1,1) NOT NULL CONSTRAINT PK_Project PRIMARY KEY,
    ProjectName  NVARCHAR(200) NOT NULL,
    CategoryID   INT           NOT NULL,
    ManagerID    INT           NOT NULL,
    SponsorID    INT           NOT NULL,
    StartDate    DATE          NOT NULL,
    EndDate      DATE          NOT NULL,
    DurationDays AS (DATEDIFF(day, StartDate, EndDate)) PERSISTED,
    CreatedBy    NVARCHAR(128) NULL,
    CreatedDate  DATETIME2     NULL,
    ModifiedBy   NVARCHAR(128) NULL,
    ModifiedDate DATETIME2     NULL,
    CONSTRAINT FK_Project_Category FOREIGN KEY (CategoryID) REFERENCES dbo.Category (CategoryID),
    CONSTRAINT FK_Project_Manager  FOREIGN KEY (ManagerID)  REFERENCES dbo.Employee (EmployeeID),
    CONSTRAINT FK_Project_Sponsor  FOREIGN KEY (SponsorID)  REFERENCES dbo.Employee (EmployeeID)
);
GO

CREATE TRIGGER dbo.trg_Project_Audit ON dbo.Project AFTER INSERT, UPDATE AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @now DATETIME2(7) = SYSUTCDATETIME();
    DECLARE @who NVARCHAR(128) = SUSER_SNAME();
    UPDATE t SET t.ModifiedBy = @who, t.ModifiedDate = @now
    FROM dbo.Project t INNER JOIN inserted i ON t.ProjectID = i.ProjectID;
    IF NOT EXISTS (SELECT 1 FROM deleted)
        UPDATE t SET t.CreatedBy = @who, t.CreatedDate = @now
        FROM dbo.Project t INNER JOIN inserted i ON t.ProjectID = i.ProjectID;
END;
GO

-- ── Self-referential nullable FK ─────────────────────────────────────────────
CREATE TABLE dbo.TaskNode (
    TaskID   INT           IDENTITY(1,1) NOT NULL CONSTRAINT PK_TaskNode PRIMARY KEY,
    Title    NVARCHAR(100) NOT NULL,
    ParentID INT           NULL,
    CONSTRAINT FK_TaskNode_Parent FOREIGN KEY (ParentID) REFERENCES dbo.TaskNode (TaskID)
);
GO

-- ── Cross-schema FK (app2 → dbo) ─────────────────────────────────────────────
-- "External" is a T-SQL reserved keyword, so the table name is bracket-quoted.
-- Reflection still reports the name as "External".
CREATE TABLE app2.[External] (
    ExternalID INT           IDENTITY(1,1) NOT NULL CONSTRAINT PK_External PRIMARY KEY,
    Name       NVARCHAR(100) NOT NULL,
    CategoryID INT           NOT NULL,
    CONSTRAINT FK_External_Category FOREIGN KEY (CategoryID) REFERENCES dbo.Category (CategoryID)
);
GO

-- ── Temporal (system-versioned) table — history excluded, period cols owned ──
CREATE TABLE dbo.Versioned (
    VersionedID INT           IDENTITY(1,1) NOT NULL CONSTRAINT PK_Versioned PRIMARY KEY,
    Name        NVARCHAR(100) NOT NULL,
    ValidFrom   DATETIME2(7)  GENERATED ALWAYS AS ROW START NOT NULL,
    ValidTo     DATETIME2(7)  GENERATED ALWAYS AS ROW END   NOT NULL,
    PERIOD FOR SYSTEM_TIME (ValidFrom, ValidTo)
) WITH (SYSTEM_VERSIONING = ON (HISTORY_TABLE = dbo.VersionedHistory));
GO

-- ── Every type, every db-owned mechanism, audit trigger ──────────────────────
CREATE TABLE dbo.AllTypes (
    AllTypesID          INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_AllTypes PRIMARY KEY,

    -- integer family
    ColBigInt           BIGINT          NULL,
    ColInt              INT             NULL,
    ColSmallInt         SMALLINT        NULL,
    ColTinyInt          TINYINT         NULL,
    ColBit              BIT             NULL,

    -- exact + approximate numerics
    ColDecimal          DECIMAL(18, 4)  NULL,
    ColNumeric          NUMERIC(10, 2)  NULL,
    ColMoney            MONEY           NULL,
    ColSmallMoney       SMALLMONEY      NULL,
    ColFloat            FLOAT           NULL,
    ColReal             REAL            NULL,

    -- date / time
    ColDate             DATE            NULL,
    ColTime             TIME(7)         NULL,
    ColDateTime         DATETIME        NULL,
    ColDateTime2        DATETIME2(7)    NULL,
    ColSmallDateTime    SMALLDATETIME   NULL,
    ColDateTimeOffset   DATETIMEOFFSET(7) NULL,

    -- strings
    ColNVarchar         NVARCHAR(100)   NULL,
    ColNVarcharMax      NVARCHAR(MAX)   NULL,
    ColVarchar          VARCHAR(50)     NULL,
    ColChar             CHAR(10)        NULL,
    ColNChar            NCHAR(10)       NULL,
    ColUniqueId         UNIQUEIDENTIFIER NULL,

    -- excluded-for-write types
    ColVarbinary        VARBINARY(100)  NULL,
    ColVarbinaryMax     VARBINARY(MAX)  NULL,
    ColXml              XML             NULL,
    ColSqlVariant       SQL_VARIANT     NULL,
    ColRowversion       ROWVERSION      NOT NULL,

    -- unknown type → str / EDITABLE fallback
    ColHierarchy        HIERARCHYID     NULL,

    -- SQL Server 2025 native types. The mssql dialect can't model either, so both
    -- are resolved from the catalog: json → EDITABLE str, vector → EXCLUDED (it
    -- reflects as varbinary). See reflection._column_flags / _display_sql_type.
    ColJson             JSON            NULL,
    ColVector           VECTOR(3)       NULL,

    -- computed
    ColComputed          AS (ColInt * 2),
    ColComputedPersisted AS (ColBigInt + 1) PERSISTED,

    -- value-generating defaults vs a plain default
    ColGenDefault       DATETIME2(7)    NOT NULL CONSTRAINT DF_AllTypes_Gen DEFAULT SYSUTCDATETIME(),
    ColNewId            UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_AllTypes_New DEFAULT NEWID(),
    ColPlainDefault     INT             NOT NULL CONSTRAINT DF_AllTypes_Plain DEFAULT 0,

    -- editable required / optional
    ColRequired         NVARCHAR(50)    NOT NULL,
    ColNullable         NVARCHAR(50)    NULL,

    -- trigger-populated audit columns
    CreatedBy           NVARCHAR(128)   NULL,
    CreatedDate         DATETIME2       NULL,
    ModifiedBy          NVARCHAR(128)   NULL,
    ModifiedDate        DATETIME2       NULL
);
GO

CREATE TRIGGER dbo.trg_AllTypes_Audit ON dbo.AllTypes AFTER INSERT, UPDATE AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @now DATETIME2(7) = SYSUTCDATETIME();
    DECLARE @who NVARCHAR(128) = SUSER_SNAME();
    UPDATE t SET t.ModifiedBy = @who, t.ModifiedDate = @now
    FROM dbo.AllTypes t INNER JOIN inserted i ON t.AllTypesID = i.AllTypesID;
    IF NOT EXISTS (SELECT 1 FROM deleted)
        UPDATE t SET t.CreatedBy = @who, t.CreatedDate = @now
        FROM dbo.AllTypes t INNER JOIN inserted i ON t.AllTypesID = i.AllTypesID;
END;
GO

-- ── Plain-typed table for CRUD round-trips ───────────────────────────────────
-- AllTypes exists for reflection breadth; Gadget mirrors the interesting
-- write-path behaviour — identity via SCOPE_IDENTITY on a trigger table, a
-- computed column, a value-generating default, a plain default, and audit
-- columns — using only plain types, so round-trip assertions stay simple.
CREATE TABLE dbo.Gadget (
    GadgetID     INT             IDENTITY(1,1) NOT NULL CONSTRAINT PK_Gadget PRIMARY KEY,
    Name         NVARCHAR(100)   NOT NULL,                                  -- required
    Quantity     INT             NULL,                                      -- nullable
    Doubled      AS (Quantity * 2),                                         -- computed
    Token        UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_Gadget_Token DEFAULT NEWID(),
    Status       INT             NOT NULL CONSTRAINT DF_Gadget_Status DEFAULT 0,
    CreatedBy    NVARCHAR(128)   NULL,
    CreatedDate  DATETIME2       NULL,
    ModifiedBy   NVARCHAR(128)   NULL,
    ModifiedDate DATETIME2       NULL
);
GO

CREATE TRIGGER dbo.trg_Gadget_Audit ON dbo.Gadget AFTER INSERT, UPDATE AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @now DATETIME2(7) = SYSUTCDATETIME();
    DECLARE @who NVARCHAR(128) = SUSER_SNAME();
    UPDATE t SET t.ModifiedBy = @who, t.ModifiedDate = @now
    FROM dbo.Gadget t INNER JOIN inserted i ON t.GadgetID = i.GadgetID;
    IF NOT EXISTS (SELECT 1 FROM deleted)
        UPDATE t SET t.CreatedBy = @who, t.CreatedDate = @now
        FROM dbo.Gadget t INNER JOIN inserted i ON t.GadgetID = i.GadgetID;
END;
GO

-- ── Rowversion table for optimistic-concurrency round-trips ──────────────────
-- A real ROWVERSION auto-increments on every UPDATE, so this exercises the full
-- If-Match flow against true SQL Server semantics: the token a read returns
-- changes after each write, and reusing a stale token is rejected with 409.
CREATE TABLE dbo.Concurrent (
    ConcurrentID INT           IDENTITY(1,1) NOT NULL CONSTRAINT PK_Concurrent PRIMARY KEY,
    Name         NVARCHAR(100) NOT NULL,
    RowVersion   ROWVERSION    NOT NULL
);
GO

-- ── CLR / spatial / sql_variant read round-trip ──────────────────────────────
-- The driver returns raw CLR-internal bytes for hierarchyid/geometry/geography,
-- and sql_variant has no fixed JSON shape. Reflection flags them
-- ColumnInfo.read_as_text and the read path CASTs them to NVARCHAR
-- (routes/crud._read_columns), so the API returns text — WKT for spatial, the
-- path for hierarchyid, the value for sql_variant. A seeded row exercises that
-- end to end. Doc (json) is a genuinely editable column alongside them.
CREATE TABLE dbo.Spatial (
    SpatialID INT           IDENTITY(1,1) NOT NULL CONSTRAINT PK_Spatial PRIMARY KEY,
    Name      NVARCHAR(100) NOT NULL,
    Geo       GEOGRAPHY     NULL,
    Shape     GEOMETRY      NULL,
    Node      HIERARCHYID   NULL,
    Variant   SQL_VARIANT   NULL,
    Doc       JSON          NULL
);
GO
INSERT INTO dbo.Spatial (Name, Geo, Shape, Node, Variant, Doc)
VALUES (N'origin',
        geography::STGeomFromText('POINT(-122 47)', 4326),
        geometry::STGeomFromText('LINESTRING(0 0, 1 1)', 0),
        '/1/2/',
        CAST(42 AS SQL_VARIANT),
        N'{"k": 1}');
GO

-- ── CHECK constraint feedback ────────────────────────────────────────────────
-- SQLAlchemy's mssql dialect doesn't reflect CHECK constraints, so reflection
-- reads them from sys.check_constraints (reflection._check_constraints) and the
-- error layer quotes the failed rule instead of only its name. A single-column
-- check lets the test assert the message names the column and quotes the rule.
CREATE TABLE dbo.Checked (
    CheckedID INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_Checked PRIMARY KEY,
    Name      NVARCHAR(100) NOT NULL,
    Score     INT           NOT NULL CONSTRAINT CK_Checked_Score CHECK (Score >= 0 AND Score <= 100)
);
GO

-- ── Schema OUTSIDE the configured set (DB_SCHEMAS = dbo, app2) ───────────────
-- dbo.EdgeRef references outside.Target. metadata.reflect(resolve_fks=True)
-- would recursively pull outside.Target into the snapshot — where it would be
-- misclassified, because the sys.* flag queries filter to the configured
-- schemas. Reflection must keep the FK metadata on EdgeRef.TargetID while
-- excluding outside.Target itself. The computed + defaulted columns exist so
-- a leak would also be visibly misclassified, not just present.
CREATE SCHEMA outside;
GO
CREATE TABLE outside.Target (
    TargetID    INT           IDENTITY(1,1) NOT NULL CONSTRAINT PK_OutsideTarget PRIMARY KEY,
    Name        NVARCHAR(50)  NOT NULL,
    ComputedCol AS (UPPER(Name)),
    StampedAt   DATETIME2     NOT NULL CONSTRAINT DF_OutsideTarget_Stamp DEFAULT SYSUTCDATETIME()
);
GO
CREATE TABLE dbo.EdgeRef (
    EdgeRefID INT          IDENTITY(1,1) NOT NULL CONSTRAINT PK_EdgeRef PRIMARY KEY,
    TargetID  INT          NOT NULL CONSTRAINT FK_EdgeRef_Target REFERENCES outside.Target (TargetID),
    Note      NVARCHAR(50) NULL
);
GO

-- ── Legacy text/ntext, alias UDT, sequence default ───────────────────────────
-- text reflects sys.columns.max_length = 16 (the LOB pointer size) and ntext 8
-- (after the nchar halving) — neither is a real limit, so reflection must not
-- surface them as max_length or the generated models reject valid input.
-- The alias UDT reflects via the dialect's base-type fallback (VARCHAR(20));
-- its sys.types row is a securable, so visibility depends on the reflection
-- identity holding VIEW DEFINITION. The sequence default is a plain
-- (overridable) default: EDITABLE and not required, but not DB-owned.
CREATE TYPE dbo.PhoneNumber FROM VARCHAR(20);
GO
CREATE SEQUENCE dbo.LegacySeq AS INT START WITH 100;
GO
CREATE TABLE dbo.Legacy (
    LegacyID   INT             IDENTITY(1,1) NOT NULL CONSTRAINT PK_Legacy PRIMARY KEY,
    ColText    TEXT            NULL,
    ColNText   NTEXT           NULL,
    ColUdt     dbo.PhoneNumber NULL,
    ColSeq     INT             NOT NULL CONSTRAINT DF_Legacy_Seq DEFAULT (NEXT VALUE FOR dbo.LegacySeq),
    Label      NVARCHAR(50)    NOT NULL
);
GO
EXEC sys.sp_addextendedproperty
    @name = N'MS_Description', @value = N'Free-text label shown to operators',
    @level0type = N'SCHEMA', @level0name = N'dbo',
    @level1type = N'TABLE',  @level1name = N'Legacy',
    @level2type = N'COLUMN', @level2name = N'Label';
GO

-- ── Manual single-column INTEGER primary key ─────────────────────────────────
-- Distinct from ManualKey (nvarchar): reflected mssql columns carry
-- autoincrement=True/False (never "auto"), so a manual int PK must stay in the
-- create model and be required — hand-built Tables of the same shape behave
-- differently, which is exactly why this is pinned against a real database.
CREATE TABLE dbo.ManualIntKey (
    IntCode INT           NOT NULL CONSTRAINT PK_ManualIntKey PRIMARY KEY,
    Label   NVARCHAR(100) NOT NULL
);
GO

-- ── Least-privilege reflection identity ──────────────────────────────────────
-- VIEW DEFINITION only — no db_datareader, no data access. Validated (Phase 0,
-- SQL Server 2025) to reflect with full parity to sa: catalog rows, gated
-- definition columns, FKs, CHECK texts, comments, and alias-UDT visibility all
-- come from VIEW DEFINITION; row access is never needed by reflection.
CREATE LOGIN reflect_vdonly WITH PASSWORD = 'Int3gration_VD!only', CHECK_POLICY = OFF;
GO
CREATE USER reflect_vdonly FOR LOGIN reflect_vdonly;
GO
GRANT VIEW DEFINITION TO reflect_vdonly;
GO

-- Seed the FK targets so CRUD round-trip tests have something to reference.
INSERT INTO dbo.Category (CategoryCode, CategoryName) VALUES (N'ENG', N'Engineering');
INSERT INTO dbo.Employee (FullName) VALUES (N'Alice Manager'), (N'Bob Sponsor');
INSERT INTO outside.Target (Name) VALUES (N'edge-target');
GO
