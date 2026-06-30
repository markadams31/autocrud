-- ============================================================================
-- Project Portfolio Management — Reference Schema & Seed Script
-- Schemas: dbo (reference / HR)  |  ppm (project management)
--
-- This script has two jobs, and every table earns its place under both:
--
--   1. A WORKED EXAMPLE for anyone designing a database to put behind this
--      app. The app builds its entire UI and API from your schema — there is
--      no per-table configuration. What you model is what you get. The tables
--      below show, concretely, how each schema decision the app reacts to
--      should be expressed. Read it top to bottom like a guide; the comment
--      over each table calls out the design rule it demonstrates.
--
--   2. A COMPREHENSIVE FIXTURE. Between them the tables cover every schema
--      feature the app reacts to — computed and GENERATED ALWAYS columns,
--      value-generating defaults, temporal history, manual / composite / identity
--      primary keys, self-referential and cross-schema foreign keys, rowversion
--      concurrency, write-excluded binary and XML types, and a high-volume table
--      for pagination — so loading it into a live database and driving the real
--      app exercises the privilege-sensitive and runtime-only paths that unit and
--      integration tests don't reach. The SCENARIO COVERAGE MAP below lists what
--      each table is here to demonstrate.
--
-- The example domain stays a project portfolio — programmes, projects,
-- milestones, tasks, people, resourcing, time, cost rates, and documents —
-- so it remains something an enterprise reader can map onto their own systems.
--
-- ----------------------------------------------------------------------------
-- Configure the app with:
--   DB_SCHEMAS=dbo,ppm
--   DB_AUDIT_COLUMNS=CreatedBy,CreatedDate,ModifiedBy,ModifiedDate
--
-- ----------------------------------------------------------------------------
-- HOW THE APP READS YOUR SCHEMA  (design rules this script demonstrates)
--
--  • Primary keys are mandatory. A table with no PK can't be addressed row by
--    row, so the app SKIPS it entirely (see dbo.IntegrationLog). Single
--    IDENTITY PKs are auto-managed and dropped from the create form; a manual
--    PK is a required input (dbo.Currency); a composite PK is addressed in the
--    URL as comma-joined values in key order (ppm.ProjectAssignment → /1,4).
--
--  • Row labels follow a name heuristic. For dropdowns and list views the app
--    picks the column whose name contains, in priority order,
--    name > label > title > description > code; else the first text column;
--    else the raw PK. Name your human-readable column accordingly. (Every
--    lookup here uses a *Name column; *Code columns lose to it on purpose.)
--
--  • Foreign keys become dropdowns labelled by the target's display column.
--    Two FKs to one table (Project.ManagerID + .SponsorID → Employee),
--    self-referential FKs (Task.ParentTaskID → Task), nullable FKs, and
--    cross-schema FKs (ppm → dbo) all work.
--
--  • Server-managed columns are detected and kept out of write forms:
--      – IDENTITY, COMPUTED (AS …), value-generating DEFAULTs (NEWID(),
--        SYSUTCDATETIME(), …), and temporal GENERATED ALWAYS period columns
--        are found STRUCTURALLY.
--      – Trigger / stored-proc-populated columns CANNOT be found structurally
--        (SQL Server exposes no metadata linking a trigger to the columns it
--        writes), so you must NAME them in DB_AUDIT_COLUMNS. Under the app's
--        OBO connection SUSER_SNAME() inside the trigger resolves to the real
--        signed-in user, so the audit trail records who actually changed each
--        row — not the application identity.
--
--  • A ROWVERSION column opts the table into optimistic concurrency. The app
--    surfaces it as a concurrency token; a client that echoes it back as
--    If-Match gets a 409 instead of silently clobbering a newer row
--    (ppm.Project). At most one per table; detected by type, so any name works.
--
--  • Write-excluded types — VARBINARY/BINARY/IMAGE, ROWVERSION, XML,
--    SQL_VARIANT — are read-only: surfaced in metadata, never writable through
--    the API (binary is hex-encoded on read). Don't make one NOT NULL without
--    a default or API inserts can't satisfy it (ppm.Attachment keeps them
--    nullable).
--
--  • NOT NULL with no default ⇒ required field. NOT NULL WITH a default ⇒
--    optional (the DB fills it). A plain default (DEFAULT 0) is a starting
--    value the client may override; a value-generating default is DB-owned.
--
--  • Types map predictably: DECIMAL/NUMERIC/MONEY → Decimal (never float, so
--    money keeps its precision); DATE/TIME/DATETIME2 → typed pickers;
--    BIT → checkbox; UNIQUEIDENTIFIER/CHAR/NVARCHAR → text.
--
--  • CHECK / UNIQUE / FK constraints are enforced by the database and surface
--    as clean CONSTRAINT_VIOLATION (409) responses. The app deliberately does
--    not re-implement them — the database stays the single source of truth.
--    The one thing it pre-validates is string max-length (a per-field 422
--    before the write is attempted).
--
--  • Authorization is entirely the database's. Grant the user group
--    SELECT/INSERT/UPDATE/DELETE per schema (see permissions.sql); the app
--    shows or hides New/Edit/Delete from each user's real grants and lets the
--    database reject anything it gets wrong.
--
-- ----------------------------------------------------------------------------
-- SCENARIO COVERAGE MAP  (what each object is here to exercise)
--
--   dbo.Currency .............. manual CHAR(3) PK · CHAR + TINYINT types ·
--                               Unicode symbols · FK target for a non-identity key
--   dbo.Department ............ simple lookup · code-vs-name heuristic
--   dbo.Employee .............. COMPUTED FullName ·
--                               value-generating DEFAULT NEWID() on a non-audit
--                               column · trigger audit columns ·
--                               UNIQUE columns · Unicode / apostrophe data
--   dbo.LaborRate ............. SYSTEM-VERSIONED temporal · GENERATED ALWAYS
--                               period columns · auto history table (hidden from
--                               the app) · MONEY type
--   dbo.IntegrationLog ........ NO primary key → skipped by reflection
--   ppm.ProjectStatus/Priority/TaskStatus ... lookups · BIT IsTerminal · colours
--   ppm.Project ............... COMPUTED DurationDays ·
--                               value-generating DEFAULT NEWID() ·
--                               ROWVERSION concurrency token · DECIMAL money ·
--                               CHECK constraints · cross-schema + dual + lookup FKs
--   ppm.Milestone ............. BIT flag · NVARCHAR(MAX) · per-row audit
--   ppm.Task .................. self-referential FK · nullable FKs · DECIMAL hours ·
--                               LIKE-wildcard ('%','_') data for search escaping
--   ppm.ProjectAssignment ..... COMPOSITE PK (/{id},{id}) · junction table · CHECK
--   ppm.Attachment ............ VARBINARY(MAX) + XML write-excluded, read-only ·
--                               binary hex-encode-on-read path
--   ppm.TimeEntry ............. VOLUME (~300 rows) for pagination / sort / range
--                               filters · REAL type · generated set-based
--
--   Two of these are worth a closer look, because the app classifies them from
--   structural sys.columns flags rather than from VIEW-DEFINITION-gated text:
--     • Computed columns (FullName, DurationDays). SQLAlchemy reads a column's
--       computed status from sys.computed_columns.definition, which SQL Server
--       hides unless the reflecting identity holds VIEW DEFINITION. The app reads
--       sys.columns.is_computed instead (visible with table access alone), so a
--       computed column is excluded from writes regardless of privilege and never
--       trips SQL Server's "cannot modify a computed column" error (271).
--     • Value-generating defaults (Employee.ExternalRef, Project.ProjectGuid =
--       NEWID()). The default's text is gated the same way; the app reads
--       sys.columns.default_object_id so a NOT NULL defaulted column isn't marked
--       required even when the text isn't visible. (Audit columns are separate —
--       matched by NAME via DB_AUDIT_COLUMNS, not by reading a definition.)
--
-- NOTE: every table that carries a trigger is reflected with implicit RETURNING
-- disabled (see reflection.py) so INSERTs use SELECT SCOPE_IDENTITY() instead of
-- an OUTPUT clause, which SQL Server forbids on a table that has a trigger.
--
-- The script is re-runnable: it drops every object in reverse dependency order
-- first (system-versioning is turned off before the temporal table is dropped).
-- ============================================================================

SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
SET NOCOUNT ON;
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'ppm')
    EXEC ('CREATE SCHEMA ppm');
GO

-- ── Drop in reverse dependency order so the script is re-runnable ─────────────
-- Triggers drop automatically with their tables. The temporal table needs
-- SYSTEM_VERSIONING turned OFF before either it or its history table can drop.

IF OBJECT_ID('ppm.TimeEntry',         'U') IS NOT NULL DROP TABLE ppm.TimeEntry;
IF OBJECT_ID('ppm.Attachment',        'U') IS NOT NULL DROP TABLE ppm.Attachment;
IF OBJECT_ID('ppm.ProjectAssignment', 'U') IS NOT NULL DROP TABLE ppm.ProjectAssignment;
IF OBJECT_ID('ppm.Task',              'U') IS NOT NULL DROP TABLE ppm.Task;
IF OBJECT_ID('ppm.Milestone',         'U') IS NOT NULL DROP TABLE ppm.Milestone;
IF OBJECT_ID('ppm.Project',           'U') IS NOT NULL DROP TABLE ppm.Project;
IF OBJECT_ID('ppm.TaskStatus',        'U') IS NOT NULL DROP TABLE ppm.TaskStatus;
IF OBJECT_ID('ppm.ProjectPriority',   'U') IS NOT NULL DROP TABLE ppm.ProjectPriority;
IF OBJECT_ID('ppm.ProjectStatus',     'U') IS NOT NULL DROP TABLE ppm.ProjectStatus;

IF OBJECT_ID('dbo.IntegrationLog',    'U') IS NOT NULL DROP TABLE dbo.IntegrationLog;

IF OBJECT_ID('dbo.LaborRate',         'U') IS NOT NULL
BEGIN
    IF EXISTS (SELECT 1 FROM sys.tables
               WHERE object_id = OBJECT_ID('dbo.LaborRate') AND temporal_type = 2)
        ALTER TABLE dbo.LaborRate SET (SYSTEM_VERSIONING = OFF);
    IF OBJECT_ID('dbo.LaborRateHistory', 'U') IS NOT NULL DROP TABLE dbo.LaborRateHistory;
    DROP TABLE dbo.LaborRate;
END

IF OBJECT_ID('dbo.Employee',          'U') IS NOT NULL DROP TABLE dbo.Employee;
IF OBJECT_ID('dbo.Department',        'U') IS NOT NULL DROP TABLE dbo.Department;
IF OBJECT_ID('dbo.Currency',          'U') IS NOT NULL DROP TABLE dbo.Currency;
GO


-- ============================================================================
-- dbo.Currency
-- A MANUAL (non-identity) primary key: the natural ISO-4217 code is the key.
-- The app keeps a manual PK in the create form as a REQUIRED field (the
-- database doesn't generate it) and addresses rows by it directly: /dbo/Currency/USD.
-- CurrencyName wins the display heuristic over CurrencyCode (name > code).
-- TINYINT and Unicode symbols broaden type coverage on a small, safe table.
-- ============================================================================
CREATE TABLE dbo.Currency (
    CurrencyCode CHAR(3)       NOT NULL,   -- manual PK (ISO 4217), e.g. 'USD' — CHAR → str
    CurrencyName NVARCHAR(50)  NOT NULL,
    Symbol       NVARCHAR(5)   NOT NULL,   -- Unicode: $ € £ ¥
    MinorUnit    TINYINT       NOT NULL CONSTRAINT DF_Currency_MinorUnit DEFAULT 2,
    CONSTRAINT PK_Currency PRIMARY KEY (CurrencyCode)
);
GO

INSERT INTO dbo.Currency (CurrencyCode, CurrencyName, Symbol, MinorUnit) VALUES
    (N'USD', N'US Dollar',      N'$', 2),
    (N'EUR', N'Euro',           N'€', 2),
    (N'GBP', N'Pound Sterling', N'£', 2),
    (N'JPY', N'Japanese Yen',   N'¥', 0);   -- MinorUnit 0 — a non-default value
GO


-- ============================================================================
-- dbo.Department
-- The simplest shape: an IDENTITY PK plus a code/name pair. DepartmentCode
-- matches the code heuristic but DepartmentName wins it (name beats code).
-- ============================================================================
CREATE TABLE dbo.Department (
    DepartmentID   INT           IDENTITY(1,1) NOT NULL,
    DepartmentCode NVARCHAR(10)  NOT NULL,
    DepartmentName NVARCHAR(100) NOT NULL,
    CONSTRAINT PK_Department      PRIMARY KEY (DepartmentID),
    CONSTRAINT UQ_Department_Code UNIQUE      (DepartmentCode)
);
GO

SET IDENTITY_INSERT dbo.Department ON;
INSERT INTO dbo.Department (DepartmentID, DepartmentCode, DepartmentName) VALUES
    (1, 'ENG',  'Engineering'),
    (2, 'PM',   'Product Management'),
    (3, 'FIN',  'Finance'),
    (4, 'OPS',  'Operations');
SET IDENTITY_INSERT dbo.Department OFF;
GO


-- ============================================================================
-- dbo.Employee
-- Carries the two privilege-sensitive column kinds (see the header note):
--   • FullName  — a COMPUTED PERSISTED column. Must be excluded from writes;
--                 the app detects it structurally (sys.columns.is_computed) so it
--                 stays write-excluded even at minimal reflection privilege.
--   • ExternalRef — a value-generating DEFAULT NEWID() on a NON-audit column.
--                 Should be DB-owned; probes whether the reflection identity
--                 recognises it as such.
-- Plus: UNIQUE business keys, a nullable DATE, and trigger-populated audit
-- columns (named in DB_AUDIT_COLUMNS, never written by the client). A handful
-- of rows carry Unicode and apostrophes to exercise NVARCHAR round-trips and
-- LIKE search.
-- ============================================================================
CREATE TABLE dbo.Employee (
    EmployeeID     INT              IDENTITY(1,1) NOT NULL,
    EmployeeNumber NVARCHAR(10)     NOT NULL,
    FirstName      NVARCHAR(50)     NOT NULL,
    LastName       NVARCHAR(50)     NOT NULL,
    FullName       AS (FirstName + ' ' + LastName) PERSISTED,   -- computed: excluded from writes
    Email          NVARCHAR(200)    NOT NULL,
    JobTitle       NVARCHAR(100)    NOT NULL,
    DepartmentID   INT              NOT NULL,
    HireDate       DATE             NULL,
    ExternalRef    UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_Employee_ExternalRef DEFAULT NEWID(),
    IsActive       BIT              NOT NULL CONSTRAINT DF_Employee_IsActive DEFAULT 1,
    CreatedBy      NVARCHAR(128)    NULL,
    CreatedDate    DATETIME2        NULL,
    ModifiedBy     NVARCHAR(128)    NULL,
    ModifiedDate   DATETIME2        NULL,
    CONSTRAINT PK_Employee        PRIMARY KEY (EmployeeID),
    CONSTRAINT UQ_Employee_Number UNIQUE      (EmployeeNumber),
    CONSTRAINT UQ_Employee_Email  UNIQUE      (Email),
    CONSTRAINT FK_Employee_Dept   FOREIGN KEY (DepartmentID) REFERENCES dbo.Department (DepartmentID)
);
GO

-- AFTER INSERT, UPDATE audit trigger. SET NOCOUNT ON keeps the extra UPDATE's
-- rowcount from confusing the driver. Created* is set only on INSERT (no rows
-- in `deleted`); Modified* is refreshed on every change. SUSER_SNAME() resolves
-- to the real caller under the app's OBO connection. Recursive triggers are off
-- by default, so the trigger's own UPDATE does not re-fire it.
CREATE TRIGGER dbo.trg_Employee_Audit ON dbo.Employee AFTER INSERT, UPDATE AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @now DATETIME2(7) = SYSUTCDATETIME();
    DECLARE @who NVARCHAR(128) = SUSER_SNAME();

    UPDATE t SET t.ModifiedBy = @who, t.ModifiedDate = @now
    FROM dbo.Employee t INNER JOIN inserted i ON t.EmployeeID = i.EmployeeID;

    IF NOT EXISTS (SELECT 1 FROM deleted)
        UPDATE t SET t.CreatedBy = @who, t.CreatedDate = @now
        FROM dbo.Employee t INNER JOIN inserted i ON t.EmployeeID = i.EmployeeID;
END;
GO

SET IDENTITY_INSERT dbo.Employee ON;
INSERT INTO dbo.Employee
    (EmployeeID, EmployeeNumber, FirstName, LastName, Email, JobTitle, DepartmentID, HireDate)
VALUES
    ( 1, 'EMP-0001', 'Alice',    'Martin',   'alice.martin@company.com',     'VP of Engineering',       1, '2018-02-12'),
    ( 2, 'EMP-0002', 'Bob',      'Chen',     'bob.chen@company.com',         'Senior Engineer',         1, '2019-06-03'),
    ( 3, 'EMP-0003', 'Carol',    'Davis',    'carol.davis@company.com',      'Software Engineer',       1, '2021-09-20'),
    ( 4, 'EMP-0004', 'David',    'Kim',      'david.kim@company.com',        'Lead Engineer',           1, '2017-11-06'),
    ( 5, 'EMP-0005', 'Emma',     'Wilson',   'emma.wilson@company.com',      'Head of Product',         2, '2016-04-18'),
    ( 6, 'EMP-0006', 'Frank',    'Lee',      'frank.lee@company.com',        'Product Manager',         2, '2020-01-13'),
    ( 7, 'EMP-0007', 'Grace',    'Thompson', 'grace.thompson@company.com',   'Chief Financial Officer', 3, '2015-08-01'),
    ( 8, 'EMP-0008', 'Henry',    'Brown',    'henry.brown@company.com',      'Head of Operations',      4, '2019-03-25'),
    ( 9, 'EMP-0009', 'Isabella', 'Jones',    'isabella.jones@company.com',   'Operations Analyst',      4, '2022-07-11'),
    (10, 'EMP-0010', 'James',    'Taylor',   'james.taylor@company.com',     'Software Engineer',       1, '2023-02-27'),
    -- Unicode and apostrophe rows — exercise NVARCHAR round-trips and search:
    (11, 'EMP-0011', N'José',    N'Núñez',   'jose.nunez@company.com',       'Senior Engineer',         1, '2020-10-05'),
    (12, 'EMP-0012', N'Mary',    N'O''Brien','mary.obrien@company.com',      'Finance Analyst',         3, '2021-05-17'),
    (13, 'EMP-0013', N'Łukasz',  N'Kowalski','lukasz.kowalski@company.com',  'DevOps Engineer',         1, '2022-12-02');
SET IDENTITY_INSERT dbo.Employee OFF;
GO


-- ============================================================================
-- dbo.LaborRate  — SYSTEM-VERSIONED TEMPORAL table
-- The textbook temporal use case: a cost rate that changes over time, where
-- you want the full history kept automatically. ValidFrom/ValidTo are
-- GENERATED ALWAYS period columns the engine owns — the app must exclude them
-- from writes (detected structurally via sys.columns.generated_always_type,
-- which db_datareader CAN see). The auto-created history table is detected by
-- its temporal_type and hidden from the app entirely. MONEY → Decimal.
-- ============================================================================
CREATE TABLE dbo.LaborRate (
    LaborRateID  INT          IDENTITY(1,1) NOT NULL,
    EmployeeID   INT          NOT NULL,
    CurrencyCode CHAR(3)      NOT NULL,
    HourlyRate   MONEY        NOT NULL,
    ValidFrom    DATETIME2(7) GENERATED ALWAYS AS ROW START NOT NULL,
    ValidTo      DATETIME2(7) GENERATED ALWAYS AS ROW END   NOT NULL,
    PERIOD FOR SYSTEM_TIME (ValidFrom, ValidTo),
    CONSTRAINT PK_LaborRate          PRIMARY KEY (LaborRateID),
    CONSTRAINT CK_LaborRate_Rate     CHECK (HourlyRate >= 0),
    CONSTRAINT FK_LaborRate_Employee FOREIGN KEY (EmployeeID)   REFERENCES dbo.Employee (EmployeeID),
    CONSTRAINT FK_LaborRate_Currency FOREIGN KEY (CurrencyCode) REFERENCES dbo.Currency (CurrencyCode)
)
WITH (SYSTEM_VERSIONING = ON (HISTORY_TABLE = dbo.LaborRateHistory));
GO

-- Period columns are engine-managed, so they are never listed on insert.
INSERT INTO dbo.LaborRate (EmployeeID, CurrencyCode, HourlyRate) VALUES
    (2, N'USD', 140.00),
    (3, N'USD', 110.00),
    (4, N'USD', 155.00),
    (6, N'EUR',  95.00),
    (9, N'GBP',  85.00);
GO

-- Apply a couple of raises so the history table actually accrues rows — each
-- UPDATE closes the current period and writes the prior row into the history
-- table automatically. The app sees only the current row; the history is hidden.
UPDATE dbo.LaborRate SET HourlyRate = 150.00 WHERE EmployeeID = 2;
UPDATE dbo.LaborRate SET HourlyRate = 120.00 WHERE EmployeeID = 3;
GO


-- ============================================================================
-- dbo.IntegrationLog  — NO PRIMARY KEY (intentional)
-- An append-only sink with no natural or surrogate key. The app can't address
-- a single row in a keyless table, so reflection SKIPS it (you'll see a
-- "Skipped N table(s) with no primary key" log line) and it never appears in
-- the sidebar. Included so the behaviour is visible and documented: if a table
-- of yours is missing from the app, the first thing to check is whether it has
-- a primary key. Give it one to make it appear.
-- ============================================================================
CREATE TABLE dbo.IntegrationLog (
    Source    NVARCHAR(100) NOT NULL,
    EventType NVARCHAR(100) NOT NULL,
    Payload   NVARCHAR(MAX) NULL,
    LoggedAt  DATETIME2     NOT NULL CONSTRAINT DF_IntegrationLog_LoggedAt DEFAULT SYSUTCDATETIME()
);
GO

INSERT INTO dbo.IntegrationLog (Source, EventType, Payload) VALUES
    (N'Azure DevOps', N'work_item.updated', N'{"id":4821,"state":"Done"}'),
    (N'Jira',         N'issue.created',     N'{"key":"PORTAL-318"}'),
    (N'Slack',        N'message.posted',    NULL);
GO


-- ============================================================================
-- ppm.ProjectStatus  —  lookup with a BIT terminal flag and a sort order.
-- ============================================================================
CREATE TABLE ppm.ProjectStatus (
    StatusID     INT          IDENTITY(1,1) NOT NULL,
    StatusName   NVARCHAR(50) NOT NULL,
    IsTerminal   BIT          NOT NULL CONSTRAINT DF_ProjectStatus_IsTerminal DEFAULT 0,
    DisplayOrder INT          NOT NULL,
    CONSTRAINT PK_ProjectStatus PRIMARY KEY (StatusID)
);
GO

SET IDENTITY_INSERT ppm.ProjectStatus ON;
INSERT INTO ppm.ProjectStatus (StatusID, StatusName, IsTerminal, DisplayOrder) VALUES
    (1, 'Initiation', 0, 1),
    (2, 'Planning',   0, 2),
    (3, 'Executing',  0, 3),
    (4, 'On Hold',    0, 4),
    (5, 'Completed',  1, 5),
    (6, 'Cancelled',  1, 6);
SET IDENTITY_INSERT ppm.ProjectStatus OFF;
GO


-- ============================================================================
-- ppm.ProjectPriority
-- ============================================================================
CREATE TABLE ppm.ProjectPriority (
    PriorityID   INT          IDENTITY(1,1) NOT NULL,
    PriorityName NVARCHAR(50) NOT NULL,
    Colour       NVARCHAR(7)  NOT NULL,
    CONSTRAINT PK_ProjectPriority PRIMARY KEY (PriorityID)
);
GO

SET IDENTITY_INSERT ppm.ProjectPriority ON;
INSERT INTO ppm.ProjectPriority (PriorityID, PriorityName, Colour) VALUES
    (1, 'Critical', '#DC2626'),
    (2, 'High',     '#EA580C'),
    (3, 'Medium',   '#CA8A04'),
    (4, 'Low',      '#16A34A');
SET IDENTITY_INSERT ppm.ProjectPriority OFF;
GO


-- ============================================================================
-- ppm.TaskStatus
-- ============================================================================
CREATE TABLE ppm.TaskStatus (
    StatusID     INT          IDENTITY(1,1) NOT NULL,
    StatusName   NVARCHAR(50) NOT NULL,
    IsTerminal   BIT          NOT NULL CONSTRAINT DF_TaskStatus_IsTerminal DEFAULT 0,
    DisplayOrder INT          NOT NULL,
    CONSTRAINT PK_TaskStatus PRIMARY KEY (StatusID)
);
GO

SET IDENTITY_INSERT ppm.TaskStatus ON;
INSERT INTO ppm.TaskStatus (StatusID, StatusName, IsTerminal, DisplayOrder) VALUES
    (1, 'Backlog',     0, 1),
    (2, 'To Do',       0, 2),
    (3, 'In Progress', 0, 3),
    (4, 'Blocked',     0, 4),
    (5, 'Done',        1, 5),
    (6, 'Cancelled',   1, 6);
SET IDENTITY_INSERT ppm.TaskStatus OFF;
GO


-- ============================================================================
-- ppm.Project  — the hub entity, and the richest single table here.
--   • DurationDays — COMPUTED PERSISTED (write-excluded, like Employee.FullName).
--   • ProjectGuid  — value-generating DEFAULT NEWID() on a non-audit column;
--                    a correlation id for external systems.
--   • RowVersion   — ROWVERSION → opts the table into optimistic concurrency.
--                    Read returns it (hex); the client echoes it as If-Match to
--                    get a 409 on a stale write instead of clobbering.
--   • Budget       — DECIMAL(18,2) money kept as Decimal (no float drift).
--   • PercentComplete — DECIMAL with a CHECK range and a plain (overridable) default.
--   • FKs: cross-schema (→ dbo.Department, dbo.Currency), dual to one table
--          (ManagerID + SponsorID → dbo.Employee), and to two ppm lookups.
--   • CHECK constraints on dates and money — surface as clean 409s.
--   • Audit columns via the trigger below.
-- ============================================================================
CREATE TABLE ppm.Project (
    ProjectID       INT              IDENTITY(1,1) NOT NULL,
    ProjectCode     NVARCHAR(20)     NOT NULL,
    ProjectName     NVARCHAR(200)    NOT NULL,
    Description     NVARCHAR(MAX)    NULL,
    StatusID        INT              NOT NULL,
    PriorityID      INT              NOT NULL,
    DepartmentID    INT              NOT NULL,
    ManagerID       INT              NOT NULL,
    SponsorID       INT              NOT NULL,
    CurrencyCode    CHAR(3)          NOT NULL,
    StartDate       DATE             NOT NULL,
    EndDate         DATE             NOT NULL,
    Budget          DECIMAL(18, 2)   NOT NULL,
    PercentComplete DECIMAL(5, 2)    NOT NULL CONSTRAINT DF_Project_PercentComplete DEFAULT 0,
    ProjectGuid     UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_Project_Guid DEFAULT NEWID(),
    DurationDays    AS (DATEDIFF(day, StartDate, EndDate)) PERSISTED,   -- computed
    RowVersion      ROWVERSION       NOT NULL,                          -- concurrency token
    CreatedBy       NVARCHAR(128)    NULL,
    CreatedDate     DATETIME2        NULL,
    ModifiedBy      NVARCHAR(128)    NULL,
    ModifiedDate    DATETIME2        NULL,
    CONSTRAINT PK_Project            PRIMARY KEY (ProjectID),
    CONSTRAINT UQ_Project_Code       UNIQUE      (ProjectCode),
    CONSTRAINT CK_Project_Dates      CHECK (EndDate >= StartDate),
    CONSTRAINT CK_Project_Budget     CHECK (Budget >= 0),
    CONSTRAINT CK_Project_Percent    CHECK (PercentComplete >= 0 AND PercentComplete <= 100),
    CONSTRAINT FK_Project_Status     FOREIGN KEY (StatusID)     REFERENCES ppm.ProjectStatus   (StatusID),
    CONSTRAINT FK_Project_Priority   FOREIGN KEY (PriorityID)   REFERENCES ppm.ProjectPriority (PriorityID),
    CONSTRAINT FK_Project_Department FOREIGN KEY (DepartmentID) REFERENCES dbo.Department      (DepartmentID),
    CONSTRAINT FK_Project_Manager    FOREIGN KEY (ManagerID)    REFERENCES dbo.Employee        (EmployeeID),
    CONSTRAINT FK_Project_Sponsor    FOREIGN KEY (SponsorID)    REFERENCES dbo.Employee        (EmployeeID),
    CONSTRAINT FK_Project_Currency   FOREIGN KEY (CurrencyCode) REFERENCES dbo.Currency        (CurrencyCode)
);
GO

CREATE TRIGGER ppm.trg_Project_Audit ON ppm.Project AFTER INSERT, UPDATE AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @now DATETIME2(7) = SYSUTCDATETIME();
    DECLARE @who NVARCHAR(128) = SUSER_SNAME();

    UPDATE t SET t.ModifiedBy = @who, t.ModifiedDate = @now
    FROM ppm.Project t INNER JOIN inserted i ON t.ProjectID = i.ProjectID;

    IF NOT EXISTS (SELECT 1 FROM deleted)
        UPDATE t SET t.CreatedBy = @who, t.CreatedDate = @now
        FROM ppm.Project t INNER JOIN inserted i ON t.ProjectID = i.ProjectID;
END;
GO

-- ProjectGuid, DurationDays, RowVersion and the audit columns are all
-- DB-managed and deliberately omitted from this INSERT.
SET IDENTITY_INSERT ppm.Project ON;
INSERT INTO ppm.Project
    (ProjectID, ProjectCode, ProjectName, Description,
     StatusID, PriorityID, DepartmentID, ManagerID, SponsorID, CurrencyCode,
     StartDate, EndDate, Budget, PercentComplete)
VALUES
    (1, 'PRJ-001', 'Cloud Infrastructure Migration',
     'Migrate on-premises workloads to Azure. Includes lift-and-shift of legacy services, '
     + 're-platforming of stateful applications, and decommission of the on-prem data centre. '
     + 'Target: 99% of production traffic served from Azure with < 200_ms p95 latency.',   -- '%' and '_' for search-escaping tests
     3, 2, 1, 1, 7, N'USD', '2025-01-06', '2025-12-31', 480000.00, 65.00),

    (2, 'PRJ-002', 'Customer Portal Redesign',
     'Full redesign of the customer-facing web portal. Modernise the UX, consolidate three '
     + 'legacy portals into one, and introduce self-service capabilities to reduce support volume.',
     2, 3, 2, 5, 1, N'EUR', '2025-03-01', '2025-09-30', 210000.00, 20.00),

    (3, 'PRJ-003', 'ERP System Upgrade',
     'Major version upgrade of the core ERP platform from v11 to v15. Includes data migration, '
     + 'integration re-mapping, and a parallel-run period to validate financial output. This is '
     + 'the largest programme in the portfolio: it touches Finance, Operations, and Procurement, '
     + 'requires sign-off from three external auditors, and carries a hard regulatory deadline at '
     + 'the close of the fiscal year. A full description is kept deliberately long here to exercise '
     + 'NVARCHAR(MAX) read round-trips through the API and the grid.',
     1, 1, 3, 7, 8, N'GBP', '2025-06-01', '2026-03-31', 750000.00, 5.00),

    (4, 'PRJ-004', 'DevOps Automation Platform',
     'Implement a unified CI/CD platform, container registry, and infrastructure-as-code pipeline '
     + 'to replace the current mix of hand-rolled scripts and manual deployments.',
     3, 2, 1, 4, 1, N'USD', '2025-02-01', '2025-08-31', 135000.00, 70.00),

    (5, 'PRJ-005', 'Enterprise Data Warehouse',
     'Design and build a centralised data warehouse to consolidate reporting across Finance, '
     + 'Operations, and Sales. Replaces twelve siloed departmental spreadsheets.',
     5, 3, 1, 2, 7, N'USD', '2024-04-01', '2024-12-20', 320000.00, 100.00);
SET IDENTITY_INSERT ppm.Project OFF;
GO


-- ============================================================================
-- ppm.Milestone
-- ============================================================================
CREATE TABLE ppm.Milestone (
    MilestoneID   INT           IDENTITY(1,1) NOT NULL,
    ProjectID     INT           NOT NULL,
    MilestoneName NVARCHAR(200) NOT NULL,
    Description   NVARCHAR(MAX) NULL,
    DueDate       DATE          NOT NULL,
    IsCompleted   BIT           NOT NULL CONSTRAINT DF_Milestone_IsCompleted DEFAULT 0,
    CreatedBy     NVARCHAR(128) NULL,
    CreatedDate   DATETIME2     NULL,
    ModifiedBy    NVARCHAR(128) NULL,
    ModifiedDate  DATETIME2     NULL,
    CONSTRAINT PK_Milestone         PRIMARY KEY (MilestoneID),
    CONSTRAINT FK_Milestone_Project FOREIGN KEY (ProjectID) REFERENCES ppm.Project (ProjectID)
);
GO

CREATE TRIGGER ppm.trg_Milestone_Audit ON ppm.Milestone AFTER INSERT, UPDATE AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @now DATETIME2(7) = SYSUTCDATETIME();
    DECLARE @who NVARCHAR(128) = SUSER_SNAME();

    UPDATE t SET t.ModifiedBy = @who, t.ModifiedDate = @now
    FROM ppm.Milestone t INNER JOIN inserted i ON t.MilestoneID = i.MilestoneID;

    IF NOT EXISTS (SELECT 1 FROM deleted)
        UPDATE t SET t.CreatedBy = @who, t.CreatedDate = @now
        FROM ppm.Milestone t INNER JOIN inserted i ON t.MilestoneID = i.MilestoneID;
END;
GO

SET IDENTITY_INSERT ppm.Milestone ON;
INSERT INTO ppm.Milestone
    (MilestoneID, ProjectID, MilestoneName, Description, DueDate, IsCompleted)
VALUES
    -- PRJ-001 Cloud Infrastructure Migration
    ( 1, 1, 'Discovery & Assessment Complete',
      'All workloads inventoried and migration wave plan approved by architecture board.',
      '2025-03-31', 1),
    ( 2, 1, 'Wave 1 — Dev & Test Migrated',
      'Non-production environments running in Azure and validated by engineering leads.',
      '2025-06-30', 1),
    ( 3, 1, 'Wave 2 — Production Cutover',
      'All production workloads live in Azure. On-premises data centre decommissioned.',
      '2025-11-30', 0),

    -- PRJ-002 Customer Portal Redesign
    ( 4, 2, 'UX Research & Prototype Signed Off',
      'User research complete and hi-fi prototype approved by product and executive stakeholders.',
      '2025-04-30', 0),
    ( 5, 2, 'Beta Release',
      'Feature-complete beta available to internal testers and a select group of pilot customers.',
      '2025-07-31', 0),
    ( 6, 2, 'Public Launch',
      'New portal live. All three legacy portal domains redirected and decommissioned.',
      '2025-09-30', 0),

    -- PRJ-003 ERP System Upgrade
    ( 7, 3, 'Requirements & Gap Analysis Complete',
      'Delta between current v11 configuration and target v15 fully documented and signed off.',
      '2025-08-31', 0),
    ( 8, 3, 'Data Migration Dry Run',
      'Full data migration rehearsal completed with Finance sign-off on accuracy and completeness.',
      '2025-11-30', 0),
    ( 9, 3, 'Parallel Run Complete',
      'Both systems running simultaneously with three consecutive reconciled accounting periods.',
      '2026-02-28', 0),

    -- PRJ-004 DevOps Automation Platform
    (10, 4, 'Pipeline Standards Published',
      'CI/CD templates and branching strategy documented, ratified by tech leads, and published to the wiki.',
      '2025-03-31', 1),
    (11, 4, 'All Repositories Onboarded',
      'Every active repository building and deploying through the new pipeline. Zero manual deployments.',
      '2025-06-30', 0),

    -- PRJ-005 Enterprise Data Warehouse (completed project)
    (12, 5, 'Source System Mapping Complete',
      'All source tables, transformation rules, and data owners documented and agreed.',
      '2024-06-30', 1),
    (13, 5, 'First Reporting Layer Live',
      'Finance and Operations dashboards operational and validated by department heads.',
      '2024-09-30', 1),
    (14, 5, 'Full Go-Live',
      'All twelve legacy spreadsheet owners migrated to warehouse reports. Spreadsheets archived.',
      '2024-12-20', 1);
SET IDENTITY_INSERT ppm.Milestone OFF;
GO


-- ============================================================================
-- ppm.Task
-- ParentTaskID is a self-referential nullable FK (subtasks). MilestoneID and
-- AssigneeID are nullable FKs. DECIMAL(6,1) hours support range filters. A few
-- task names embed '%' and '_' to exercise LIKE-wildcard escaping in search.
-- Rows are inserted parents-before-subtasks to satisfy the self-referential FK.
-- ============================================================================
CREATE TABLE ppm.Task (
    TaskID         INT           IDENTITY(1,1) NOT NULL,
    ProjectID      INT           NOT NULL,
    MilestoneID    INT           NULL,
    ParentTaskID   INT           NULL,
    TaskName       NVARCHAR(200) NOT NULL,
    Description    NVARCHAR(MAX) NULL,
    AssigneeID     INT           NULL,
    StatusID       INT           NOT NULL,
    EstimatedHours DECIMAL(6, 1) NULL,
    ActualHours    DECIMAL(6, 1) NULL,
    DueDate        DATE          NULL,
    CreatedBy      NVARCHAR(128) NULL,
    CreatedDate    DATETIME2     NULL,
    ModifiedBy     NVARCHAR(128) NULL,
    ModifiedDate   DATETIME2     NULL,
    CONSTRAINT PK_Task           PRIMARY KEY (TaskID),
    CONSTRAINT FK_Task_Project   FOREIGN KEY (ProjectID)    REFERENCES ppm.Project    (ProjectID),
    CONSTRAINT FK_Task_Milestone FOREIGN KEY (MilestoneID)  REFERENCES ppm.Milestone  (MilestoneID),
    CONSTRAINT FK_Task_Parent    FOREIGN KEY (ParentTaskID) REFERENCES ppm.Task       (TaskID),
    CONSTRAINT FK_Task_Assignee  FOREIGN KEY (AssigneeID)   REFERENCES dbo.Employee   (EmployeeID),
    CONSTRAINT FK_Task_Status    FOREIGN KEY (StatusID)     REFERENCES ppm.TaskStatus (StatusID)
);
GO

CREATE TRIGGER ppm.trg_Task_Audit ON ppm.Task AFTER INSERT, UPDATE AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @now DATETIME2(7) = SYSUTCDATETIME();
    DECLARE @who NVARCHAR(128) = SUSER_SNAME();

    UPDATE t SET t.ModifiedBy = @who, t.ModifiedDate = @now
    FROM ppm.Task t INNER JOIN inserted i ON t.TaskID = i.TaskID;

    IF NOT EXISTS (SELECT 1 FROM deleted)
        UPDATE t SET t.CreatedBy = @who, t.CreatedDate = @now
        FROM ppm.Task t INNER JOIN inserted i ON t.TaskID = i.TaskID;
END;
GO

SET IDENTITY_INSERT ppm.Task ON;

-- ── PRJ-001 Cloud Infrastructure Migration ────────────────────────────────────
INSERT INTO ppm.Task
    (TaskID, ProjectID, MilestoneID, ParentTaskID, TaskName, Description, AssigneeID, StatusID, EstimatedHours, ActualHours, DueDate)
VALUES
    (1, 1, 1, NULL, 'Inventory on-premises servers',
     'Catalogue all physical and virtual servers with owner, OS version, and utilisation data.',
     4, 5, 24.0, 26.5, '2025-02-14'),
    (2, 1, 1, NULL, 'Map application dependencies',
     'Document inter-service dependencies to determine migration order and minimise outage risk.',
     2, 5, 32.0, 35.0, '2025-03-14'),
    (3, 1, 1, NULL, 'Produce migration wave plan',
     'Group workloads into waves based on complexity, dependency order, and business criticality.',
     1, 5, 16.0, 14.0, '2025-03-28'),
    (4, 1, 2, NULL, 'Provision Azure landing zone',
     'Deploy hub-spoke network topology, policies, and RBAC baseline via Terraform.',
     4, 5, 40.0, 52.0, '2025-04-30'),
    (5, 1, 2, NULL, 'Migrate dev environment',
     'Lift-and-shift dev workloads to Azure. Validate application starts and dependencies resolve.',
     3, 5, 24.0, 20.0, '2025-05-31'),
    (6, 1, 2, 5, 'Validate dev smoke tests',
     'Run the existing automated test suite against the migrated dev environment.',
     3, 5, 8.0, 9.5, '2025-06-07'),
    (7, 1, 2, NULL, 'Migrate test environment',
     NULL, 3, 5, 24.0, 22.0, '2025-06-20'),
    (8, 1, 3, NULL, 'Production migration dry run',
     'Full rehearsal of production cutover procedure with rollback plan verified end-to-end.',
     4, 3, 40.0, NULL, '2025-09-30'),
    (9, 1, 3, NULL, 'Stakeholder go / no-go sign-off',
     NULL, 1, 2, 4.0, NULL, '2025-10-15'),
    (10, 1, 3, NULL, 'Production cutover',
     'Execute migration during approved maintenance window per the rehearsed runbook.',
     4, 1, 48.0, NULL, '2025-11-22'),
    (11, 1, 3, 10, 'Decommission on-premises servers',
     'Raise decommission requests, confirm hardware return, and close data centre contract.',
     4, 1, 16.0, NULL, '2025-11-30'),
    (12, 1, NULL, NULL, 'Weekly status reporting',
     'Produce and distribute the project status report every Friday throughout the project.',
     1, 3, 2.0, NULL, NULL);

-- ── PRJ-002 Customer Portal Redesign ─────────────────────────────────────────
INSERT INTO ppm.Task
    (TaskID, ProjectID, MilestoneID, ParentTaskID, TaskName, Description, AssigneeID, StatusID, EstimatedHours, ActualHours, DueDate)
VALUES
    (13, 2, 4, NULL, 'Conduct user interviews',
     'Interview 20 customers across enterprise and SMB segments. Recruit via account managers.',
     6, 5, 40.0, 44.0, '2025-04-07'),
    (14, 2, 4, NULL, 'Synthesise research findings',
     NULL, 5, 5, 16.0, 18.0, '2025-04-18'),
    (15, 2, 4, NULL, 'Produce hi-fi prototype',
     'Interactive Figma prototype covering all primary user journeys. Must pass internal design review.',
     6, 3, 60.0, NULL, '2025-04-28'),
    (16, 2, 5, NULL, 'Build authentication & SSO layer',
     NULL, 3, 2, 32.0, NULL, '2025-06-15'),
    (17, 2, 5, NULL, 'Implement self-service account section',
     NULL, 10, 2, 48.0, NULL, '2025-07-10'),
    (18, 2, 5, 17, 'Write unit tests for account section',
     NULL, 10, 1, 16.0, NULL, '2025-07-15'),
    (19, 2, 6, NULL, 'Accessibility & SEO audit',
     'Verify WCAG 2.1 AA compliance across all pages. Add baseline SEO metadata and sitemap.',
     6, 1, 24.0, NULL, '2025-09-01'),
    (20, 2, 6, NULL, 'Legacy portal redirect configuration',
     'Configure HTTP 301 redirects from all three legacy portal domains to the new portal.',
     3, 1, 8.0, NULL, '2025-09-25');

-- ── PRJ-003 ERP System Upgrade ────────────────────────────────────────────────
INSERT INTO ppm.Task
    (TaskID, ProjectID, MilestoneID, ParentTaskID, TaskName, Description, AssigneeID, StatusID, EstimatedHours, ActualHours, DueDate)
VALUES
    (21, 3, 7, NULL, 'Review v11 to v15 upgrade guide',
     NULL, 7, 2, 16.0, NULL, '2025-07-15'),
    (22, 3, 7, NULL, 'Document custom module delta',
     'Identify every custom module that requires changes or a replacement in v15.',
     9, 1, 40.0, NULL, '2025-08-15'),
    (23, 3, 8, NULL, 'Extract and transform source data',
     'Run ETL jobs against v11 production data and load into the v15 test instance.',
     9, 1, 60.0, NULL, '2025-11-01'),
    (24, 3, 9, NULL, 'Finance reconciliation during parallel run',
     'Reconcile GL output between v11 and v15 for three consecutive accounting periods.',
     7, 1, 80.0, NULL, '2026-02-14');

-- ── PRJ-004 DevOps Automation Platform ───────────────────────────────────────
INSERT INTO ppm.Task
    (TaskID, ProjectID, MilestoneID, ParentTaskID, TaskName, Description, AssigneeID, StatusID, EstimatedHours, ActualHours, DueDate)
VALUES
    (25, 4, 10, NULL, 'Draft CI/CD pipeline templates',
     'Create reusable Azure Pipelines YAML templates for build, test, and deploy stages.',
     4, 5, 24.0, 22.0, '2025-03-14'),
    (26, 4, 10, NULL, 'Define branching strategy',
     'Document and ratify Git flow conventions. Circulate for tech lead review and approval.',
     4, 5, 8.0, 6.0, '2025-03-21'),
    (27, 4, 11, NULL, 'Onboard backend service repositories',
     NULL, 4, 3, 32.0, NULL, '2025-05-31'),
    (28, 4, 11, NULL, 'Onboard frontend repositories',
     NULL, 10, 4, 24.0, NULL, '2025-05-31'),
    (29, 4, 11, 28, 'Resolve Node version conflict in pipeline',
     'Build fails on Node 18 — determine whether to pin a lower version or update the Dockerfile.',
     10, 3, 4.0, NULL, '2025-06-07'),
    -- '%' and '_' in the name exercise LIKE-wildcard escaping: a search for
    -- "95%" or "auth_module" must match these literally, not as wildcards.
    (30, 4, 11, NULL, 'Hit 95% test coverage on auth_module',
     'Raise automated coverage on the authentication module from 78% to at least 95%.',
     2, 3, 12.0, NULL, '2025-06-20');

-- ── PRJ-005 Enterprise Data Warehouse (completed) ─────────────────────────────
INSERT INTO ppm.Task
    (TaskID, ProjectID, MilestoneID, ParentTaskID, TaskName, Description, AssigneeID, StatusID, EstimatedHours, ActualHours, DueDate)
VALUES
    (31, 5, 12, NULL, 'Map Finance source tables',
     NULL, 2, 5, 16.0, 14.0, '2024-05-31'),
    (32, 5, 12, NULL, 'Map Operations source tables',
     NULL, 9, 5, 16.0, 18.0, '2024-06-14'),
    (33, 5, 13, NULL, 'Build Finance dimension and fact tables',
     NULL, 2, 5, 32.0, 36.0, '2024-08-31'),
    (34, 5, 13, NULL, 'Build Operations fact tables',
     NULL, 2, 5, 40.0, 44.0, '2024-09-20'),
    (35, 5, 14, NULL, 'Migrate spreadsheet owners to warehouse',
     'Provide hands-on training sessions and migrate each of the 12 teams to warehouse reports.',
     8, 5, 24.0, 30.0, '2024-12-15'),
    (36, 5, 14, NULL, 'Decommission legacy spreadsheets',
     'Archive final versions, revoke edit access, and confirm each owner is self-sufficient.',
     9, 5, 4.0, 3.0, '2024-12-20');

SET IDENTITY_INSERT ppm.Task OFF;
GO


-- ============================================================================
-- ppm.ProjectAssignment  — COMPOSITE PRIMARY KEY junction table
-- Resourcing: which people are assigned to which projects, in what role, at
-- what allocation. The PK is (ProjectID, EmployeeID), so a row is addressed in
-- the URL as comma-joined key values in key order — e.g. /ppm/ProjectAssignment/1,4.
-- RoleName wins the display heuristic. AllocationPercent carries a CHECK.
-- ============================================================================
CREATE TABLE ppm.ProjectAssignment (
    ProjectID         INT           NOT NULL,
    EmployeeID        INT           NOT NULL,
    RoleName          NVARCHAR(100) NOT NULL,
    AllocationPercent DECIMAL(5, 2) NOT NULL,
    StartDate         DATE          NOT NULL,
    EndDate           DATE          NULL,
    CreatedBy         NVARCHAR(128) NULL,
    CreatedDate       DATETIME2     NULL,
    ModifiedBy        NVARCHAR(128) NULL,
    ModifiedDate      DATETIME2     NULL,
    CONSTRAINT PK_ProjectAssignment    PRIMARY KEY (ProjectID, EmployeeID),
    CONSTRAINT CK_PA_Allocation        CHECK (AllocationPercent > 0 AND AllocationPercent <= 100),
    CONSTRAINT FK_PA_Project           FOREIGN KEY (ProjectID)  REFERENCES ppm.Project  (ProjectID),
    CONSTRAINT FK_PA_Employee          FOREIGN KEY (EmployeeID) REFERENCES dbo.Employee (EmployeeID)
);
GO

CREATE TRIGGER ppm.trg_ProjectAssignment_Audit ON ppm.ProjectAssignment AFTER INSERT, UPDATE AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @now DATETIME2(7) = SYSUTCDATETIME();
    DECLARE @who NVARCHAR(128) = SUSER_SNAME();

    UPDATE t SET t.ModifiedBy = @who, t.ModifiedDate = @now
    FROM ppm.ProjectAssignment t
    INNER JOIN inserted i ON t.ProjectID = i.ProjectID AND t.EmployeeID = i.EmployeeID;

    IF NOT EXISTS (SELECT 1 FROM deleted)
        UPDATE t SET t.CreatedBy = @who, t.CreatedDate = @now
        FROM ppm.ProjectAssignment t
        INNER JOIN inserted i ON t.ProjectID = i.ProjectID AND t.EmployeeID = i.EmployeeID;
END;
GO

INSERT INTO ppm.ProjectAssignment (ProjectID, EmployeeID, RoleName, AllocationPercent, StartDate, EndDate) VALUES
    (1,  1, N'Project Manager',      50.00, '2025-01-06', '2025-12-31'),
    (1,  4, N'Technical Lead',       80.00, '2025-01-06', '2025-12-31'),
    (1,  3, N'Cloud Engineer',      100.00, '2025-01-06', '2025-08-31'),
    (1, 13, N'DevOps Engineer',      60.00, '2025-02-01', '2025-12-31'),
    (2,  5, N'Project Sponsor',      20.00, '2025-03-01', NULL),
    (2,  6, N'Product Manager',      75.00, '2025-03-01', '2025-09-30'),
    (2,  3, N'Frontend Engineer',   100.00, '2025-05-01', '2025-09-30'),
    (3,  7, N'Programme Director',   40.00, '2025-06-01', NULL),
    (3,  9, N'Data Migration Lead', 100.00, '2025-06-01', '2026-03-31'),
    (3, 12, N'Finance Analyst',      50.00, '2025-07-01', '2026-03-31'),
    (4,  4, N'Platform Lead',        70.00, '2025-02-01', '2025-08-31'),
    (4, 10, N'Build Engineer',      100.00, '2025-02-01', '2025-08-31'),
    (5,  2, N'Data Architect',       30.00, '2024-04-01', '2024-12-20');
GO


-- ============================================================================
-- ppm.Attachment  — WRITE-EXCLUDED types surfaced READ-ONLY
-- Documents pinned to a project. FileBytes (VARBINARY(MAX)) and Metadata (XML)
-- are types the app never lets a client write: they appear in metadata but not
-- in the create/update form, and on read the binary is hex-encoded. Both are
-- NULLABLE — a write-excluded NOT NULL column with no default would make API
-- inserts impossible, so don't model one that way. FileName is the display column.
-- ============================================================================
CREATE TABLE ppm.Attachment (
    AttachmentID INT            IDENTITY(1,1) NOT NULL,
    ProjectID    INT            NOT NULL,
    UploadedByID INT            NULL,
    FileName     NVARCHAR(260)  NOT NULL,
    ContentType  NVARCHAR(100)  NOT NULL,
    FileBytes    VARBINARY(MAX) NULL,   -- write-excluded; hex-encoded on read
    Metadata     XML            NULL,   -- write-excluded; read-only
    CreatedBy    NVARCHAR(128)  NULL,
    CreatedDate  DATETIME2      NULL,
    ModifiedBy   NVARCHAR(128)  NULL,
    ModifiedDate DATETIME2      NULL,
    CONSTRAINT PK_Attachment         PRIMARY KEY (AttachmentID),
    CONSTRAINT FK_Attachment_Project FOREIGN KEY (ProjectID)    REFERENCES ppm.Project  (ProjectID),
    CONSTRAINT FK_Attachment_Uploader FOREIGN KEY (UploadedByID) REFERENCES dbo.Employee (EmployeeID)
);
GO

CREATE TRIGGER ppm.trg_Attachment_Audit ON ppm.Attachment AFTER INSERT, UPDATE AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @now DATETIME2(7) = SYSUTCDATETIME();
    DECLARE @who NVARCHAR(128) = SUSER_SNAME();

    UPDATE t SET t.ModifiedBy = @who, t.ModifiedDate = @now
    FROM ppm.Attachment t INNER JOIN inserted i ON t.AttachmentID = i.AttachmentID;

    IF NOT EXISTS (SELECT 1 FROM deleted)
        UPDATE t SET t.CreatedBy = @who, t.CreatedDate = @now
        FROM ppm.Attachment t INNER JOIN inserted i ON t.AttachmentID = i.AttachmentID;
END;
GO

INSERT INTO ppm.Attachment (ProjectID, UploadedByID, FileName, ContentType, FileBytes, Metadata) VALUES
    (1, 4, N'migration-runbook.pdf',  N'application/pdf',
     0x255044462D312E340A25E2E3CFD3,                       -- a few bytes of a PDF header
     CAST(N'<doc pages="12" classification="internal" reviewed="true" />' AS XML)),
    (2, 6, N'portal-wireframes.fig',  N'application/octet-stream',
     0x46494701000102030405,
     CAST(N'<doc tool="Figma" version="3" />' AS XML)),
    (3, 7, N'erp-gap-analysis.xlsx',  N'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
     NULL, NULL);                                          -- both write-excluded columns null
GO


-- ============================================================================
-- ppm.TimeEntry  — VOLUME table for pagination / sorting / range filters
-- Timesheet lines: high row count, so the grid pages (default 50/page → ~6
-- pages), sorts stably, and supports range filters on Hours and EntryDate and
-- free-text search on Notes. Generated set-based rather than hand-written so
-- the file stays readable while the table stays large. REAL → float coverage
-- via BillablePercent.
-- ============================================================================
CREATE TABLE ppm.TimeEntry (
    TimeEntryID     INT           IDENTITY(1,1) NOT NULL,
    TaskID          INT           NOT NULL,
    EmployeeID      INT           NOT NULL,
    EntryDate       DATE          NOT NULL,
    Hours           DECIMAL(5, 2) NOT NULL,
    BillablePercent REAL          NULL,
    Notes           NVARCHAR(400) NULL,
    CreatedBy       NVARCHAR(128) NULL,
    CreatedDate     DATETIME2     NULL,
    ModifiedBy      NVARCHAR(128) NULL,
    ModifiedDate    DATETIME2     NULL,
    CONSTRAINT PK_TimeEntry          PRIMARY KEY (TimeEntryID),
    CONSTRAINT CK_TimeEntry_Hours    CHECK (Hours > 0 AND Hours <= 24),
    CONSTRAINT FK_TimeEntry_Task     FOREIGN KEY (TaskID)     REFERENCES ppm.Task     (TaskID),
    CONSTRAINT FK_TimeEntry_Employee FOREIGN KEY (EmployeeID) REFERENCES dbo.Employee (EmployeeID)
);
GO

CREATE TRIGGER ppm.trg_TimeEntry_Audit ON ppm.TimeEntry AFTER INSERT, UPDATE AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @now DATETIME2(7) = SYSUTCDATETIME();
    DECLARE @who NVARCHAR(128) = SUSER_SNAME();

    UPDATE t SET t.ModifiedBy = @who, t.ModifiedDate = @now
    FROM ppm.TimeEntry t INNER JOIN inserted i ON t.TimeEntryID = i.TimeEntryID;

    IF NOT EXISTS (SELECT 1 FROM deleted)
        UPDATE t SET t.CreatedBy = @who, t.CreatedDate = @now
        FROM ppm.TimeEntry t INNER JOIN inserted i ON t.TimeEntryID = i.TimeEntryID;
END;
GO

-- Generate ~300 timesheet rows by spreading a tally over the seeded tasks.
-- Each entry inherits its task's assignee (falling back to a valid employee
-- when the task is unassigned), a date within the last ~90 days, and a plausible
-- hours value. No literal value is interpolated — this is a set-based INSERT.
;WITH Tasks AS (
    SELECT TaskID, AssigneeID,
           ROW_NUMBER() OVER (ORDER BY TaskID) - 1 AS idx,
           COUNT(*)     OVER ()                     AS cnt
    FROM ppm.Task
),
Tally AS (
    SELECT TOP (300) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS n
    FROM sys.all_objects
)
INSERT INTO ppm.TimeEntry (TaskID, EmployeeID, EntryDate, Hours, BillablePercent, Notes)
SELECT
    tk.TaskID,
    ISNULL(tk.AssigneeID, 1 + (tl.n % 10)),                       -- 1..10, all valid employees
    DATEADD(day, -(tl.n % 90), CAST('2025-06-30' AS DATE)),
    CAST(0.5 + (tl.n % 16) * 0.5 AS DECIMAL(5, 2)),               -- 0.5 .. 8.0 hours
    CASE WHEN tl.n % 5 = 0 THEN NULL
         ELSE CAST(50 + (tl.n % 51) AS REAL) END,                 -- 50.0 .. 100.0, or NULL
    CASE WHEN tl.n % 7 = 0 THEN N'Logged via mobile timesheet'
         WHEN tl.n % 7 = 3 THEN N'Re-work after review'
         ELSE NULL END
FROM Tally tl
JOIN Tasks tk ON tk.idx = tl.n % tk.cnt;
GO


-- ============================================================================
-- Verification queries — run after seeding to confirm everything loaded and to
-- see the edge cases at a glance.
-- ============================================================================

-- Row counts across every reflected table (IntegrationLog is keyless and is
-- expected NOT to appear in the app, though it has rows here).
SELECT 'dbo.Currency'           AS [Table], COUNT(*) AS Rows FROM dbo.Currency
UNION ALL SELECT 'dbo.Department',          COUNT(*) FROM dbo.Department
UNION ALL SELECT 'dbo.Employee',            COUNT(*) FROM dbo.Employee
UNION ALL SELECT 'dbo.LaborRate',           COUNT(*) FROM dbo.LaborRate
UNION ALL SELECT 'dbo.IntegrationLog (no PK — app skips)', COUNT(*) FROM dbo.IntegrationLog
UNION ALL SELECT 'ppm.ProjectStatus',       COUNT(*) FROM ppm.ProjectStatus
UNION ALL SELECT 'ppm.ProjectPriority',     COUNT(*) FROM ppm.ProjectPriority
UNION ALL SELECT 'ppm.TaskStatus',          COUNT(*) FROM ppm.TaskStatus
UNION ALL SELECT 'ppm.Project',             COUNT(*) FROM ppm.Project
UNION ALL SELECT 'ppm.Milestone',           COUNT(*) FROM ppm.Milestone
UNION ALL SELECT 'ppm.Task',                COUNT(*) FROM ppm.Task
UNION ALL SELECT 'ppm.ProjectAssignment',   COUNT(*) FROM ppm.ProjectAssignment
UNION ALL SELECT 'ppm.Attachment',          COUNT(*) FROM ppm.Attachment
UNION ALL SELECT 'ppm.TimeEntry',           COUNT(*) FROM ppm.TimeEntry;

-- Audit columns populated by the trigger — CreatedBy/ModifiedBy show whoever
-- ran this seed. When the app writes a row, these instead show the signed-in
-- end user (SUSER_SNAME() under the OBO connection).
SELECT TOP 5 EmployeeID, FullName, ExternalRef, CreatedBy, ModifiedBy
FROM dbo.Employee ORDER BY EmployeeID;

-- Computed + value-generating + concurrency columns resolved by the database.
SELECT ProjectID, ProjectName, DurationDays, ProjectGuid,
       CONVERT(VARCHAR(34), RowVersion, 1) AS RowVersionHex, Budget, CurrencyCode
FROM ppm.Project ORDER BY ProjectID;

-- Temporal history accrued automatically by the two UPDATEs above.
SELECT 'current' AS [Set], COUNT(*) AS Rows FROM dbo.LaborRate
UNION ALL SELECT 'history', COUNT(*) FROM dbo.LaborRateHistory;

-- Self-referential FK — subtasks with their parent task names.
SELECT c.TaskID, c.TaskName AS SubtaskName, p.TaskID AS ParentTaskID, p.TaskName AS ParentTaskName
FROM ppm.Task c JOIN ppm.Task p ON c.ParentTaskID = p.TaskID
ORDER BY p.TaskID, c.TaskID;

-- Two FKs to the same Employee table — both resolve to dbo.Employee rows.
-- (In the UI each dropdown is labelled by Employee's display column. Because
-- FullName is computed — hence DB-owned and not a display candidate — the app
-- labels the options by FirstName, the first editable column matching the name
-- heuristic; this query shows the stored FullName for readability.)
SELECT pr.ProjectName, mgr.FullName AS Manager, spn.FullName AS Sponsor
FROM ppm.Project pr
JOIN dbo.Employee mgr ON pr.ManagerID = mgr.EmployeeID
JOIN dbo.Employee spn ON pr.SponsorID = spn.EmployeeID
ORDER BY pr.ProjectID;

-- Composite-PK sample — the URL key for each row is "ProjectID,EmployeeID".
SELECT TOP 5 CONCAT(ProjectID, ',', EmployeeID) AS UrlKey, RoleName, AllocationPercent
FROM ppm.ProjectAssignment ORDER BY ProjectID, EmployeeID;
