#!/bin/bash
# init-local.sh — one-shot database bootstrap for the docker-compose local mode.
#
# Runs inside the SQL Server image (which ships sqlcmd in /opt/mssql-tools18)
# as the `db-init` service: waits for the `db` service to accept logins,
# creates the application database if it doesn't exist, and loads the demo
# schema + data (database/seed.sql) exactly once.
#
# Seeding is deliberately once-only: seed.sql is destructive by design (it
# drops and recreates every table so hosted re-seeds are deterministic), so
# re-running it on every `docker compose up` would silently wipe any edits made
# through the app. A sentinel table check skips the seed when data already
# exists; reset everything with `docker compose down -v`.
#
# -C trusts the container's self-signed certificate; -b makes sqlcmd exit
# non-zero on SQL errors so a failed seed fails the service (and, through
# depends_on, stops the app from starting against a half-seeded database).

set -euo pipefail

SQLCMD=/opt/mssql-tools18/bin/sqlcmd
DB_NAME="${DB_NAME:-autocrud}"

echo "Waiting for SQL Server to accept connections..."
for i in $(seq 1 60); do
    if "$SQLCMD" -S db -U sa -P "$MSSQL_SA_PASSWORD" -C -b -Q "SELECT 1" -o /dev/null 2>/dev/null; then
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "SQL Server never became reachable" >&2
        exit 1
    fi
    sleep 2
done
echo "SQL Server is up."

"$SQLCMD" -S db -U sa -P "$MSSQL_SA_PASSWORD" -C -b \
    -Q "IF DB_ID('$DB_NAME') IS NULL CREATE DATABASE [$DB_NAME];"

SEEDED=$("$SQLCMD" -S db -U sa -P "$MSSQL_SA_PASSWORD" -C -b -d "$DB_NAME" -h -1 -W \
    -Q "SET NOCOUNT ON; SELECT IIF(OBJECT_ID('dbo.Currency', 'U') IS NULL, 0, 1);")

if [ "$(echo "$SEEDED" | tr -d '[:space:]')" = "1" ]; then
    echo "Database '$DB_NAME' is already seeded - leaving your data alone."
    echo "(Run 'docker compose down -v' first if you want a fresh demo dataset.)"
    exit 0
fi

echo "Seeding demo schema + data (database/seed.sql)..."
"$SQLCMD" -S db -U sa -P "$MSSQL_SA_PASSWORD" -C -b -d "$DB_NAME" -i /database/seed.sql
echo "Seed complete."
