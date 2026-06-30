-- ============================================================================
-- Database Permissions
--
-- Two identities connect to this database — each is handled separately:
--
-- 1. App Service managed identity  — schema reflection only (reads sys.* metadata)
--    Created automatically by Terraform (null_resource in sql.tf).
--    Its only grant is VIEW DEFINITION. That makes every table and column visible
--    in the sys.* catalog views AND reveals the computed-column and default-
--    constraint definition text SQL Server otherwise hides from a principal
--    lacking it (without which reflection mis-classifies those columns as
--    writable). db_datareader is deliberately NOT granted — this identity never
--    reads data rows; all data access uses the signed-in user's OBO token.
--    Terraform applies the grant — no action required here. For a manual
--    (non-Terraform) setup, run as a DB admin:
--      CREATE USER [<app-service-name>] FROM EXTERNAL PROVIDER;
--      GRANT VIEW DEFINITION TO [<app-service-name>];
--
-- 2. Signed-in users (EasyAuth OBO token) — all data access
--    EasyAuth acquires a SQL-scoped token for the authenticated user and
--    injects it as X-MS-TOKEN-AAD-ACCESS-TOKEN. The app passes it directly
--    to the ODBC driver, so SQL Server sees the real caller's identity.
--    Authorization is enforced entirely by SQL grants — the app enforces none.
--
-- This script creates a contained database user for the Entra security group
-- provisioned by Terraform and grants it CRUD access to the application schemas.
--
-- Run this script once per environment after terraform apply.
-- The group name is printed by Terraform as the `app_users_group_name` output.
-- ============================================================================

-- ── Set the security group name from the Terraform output ─────────────────────
--    Replace this value with the app_users_group_name output for the environment.
--    Dev:  autocrud-dev-users
--    Prod: autocrud-prod-users
DECLARE @GroupName NVARCHAR(200) = N'autocrud-dev-users';

-- ── Create the contained database user for the security group ─────────────────
--    Type 'X' = external group (Entra security group).
--    Members of this group inherit all permissions granted to it.
DECLARE @sql NVARCHAR(MAX);

IF NOT EXISTS (
    SELECT 1 FROM sys.database_principals
    WHERE name = @GroupName AND type_desc = 'EXTERNAL_GROUPS'
)
BEGIN
    SET @sql = N'CREATE USER [' + @GroupName + N'] FROM EXTERNAL PROVIDER';
    EXEC sp_executesql @sql;
    PRINT 'Created contained user: ' + @GroupName;
END
ELSE
BEGIN
    PRINT 'User already exists, skipping CREATE: ' + @GroupName;
END

-- ── Grant CRUD permissions on application schemas ─────────────────────────────
--    Schema-level grants are preferred over db_datareader / db_datawriter —
--    they scope access precisely to the two application schemas and nothing else.
--
--    GRANT is idempotent: re-running this script on an existing user is safe.

SET @sql = N'GRANT SELECT, INSERT, UPDATE, DELETE ON SCHEMA::dbo TO [' + @GroupName + N']';
EXEC sp_executesql @sql;
PRINT 'Granted dbo schema permissions to: ' + @GroupName;

SET @sql = N'GRANT SELECT, INSERT, UPDATE, DELETE ON SCHEMA::ppm TO [' + @GroupName + N']';
EXEC sp_executesql @sql;
PRINT 'Granted ppm schema permissions to: ' + @GroupName;

-- ── Verify ────────────────────────────────────────────────────────────────────
SELECT
    dp.name                 AS Principal,
    dp.type_desc            AS PrincipalType,
    perm.class_desc         AS PermissionScope,
    perm.permission_name    AS Permission,
    perm.state_desc         AS State,
    OBJECT_NAME(perm.major_id) AS [Object]
FROM sys.database_permissions perm
JOIN sys.database_principals  dp   ON perm.grantee_principal_id = dp.principal_id
WHERE dp.name = @GroupName
ORDER BY perm.class_desc, perm.permission_name;
