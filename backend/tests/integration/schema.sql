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

-- ── Fetch-safe table for CRUD round-trips ────────────────────────────────────
-- AllTypes intentionally contains types pyodbc cannot fetch back via SELECT *
-- (hierarchyid, sql_variant), which is fine for metadata reflection but not for
-- row round-trips. Gadget mirrors the interesting write-path behaviour —
-- identity via SCOPE_IDENTITY on a trigger table, a computed column, a
-- value-generating default, a plain default, and audit columns — using only
-- fetch-safe types.
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

-- Seed the FK targets so CRUD round-trip tests have something to reference.
INSERT INTO dbo.Category (CategoryCode, CategoryName) VALUES (N'ENG', N'Engineering');
INSERT INTO dbo.Employee (FullName) VALUES (N'Alice Manager'), (N'Bob Sponsor');
GO
