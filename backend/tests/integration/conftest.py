"""
Tier-3 integration fixtures — a real SQL Server in Docker via Testcontainers.

Everything here is gated:
  - skipped if `testcontainers` isn't installed,
  - skipped if no `ODBC Driver 1x for SQL Server` is available locally,
  - skipped if Docker can't start the container.

So a plain `pytest` run still works without Docker; these light up only when
the environment can support them. The container is driven directly through the
generic DockerContainer + pyodbc (no pymssql dependency for readiness).
"""

import os
import re
import subprocess
import time
import urllib.parse

import pytest

# Skip the whole tier if the optional dependency isn't present.
pytest.importorskip("testcontainers", reason="testcontainers not installed (uv sync --extra test)")

from testcontainers.core.container import DockerContainer  # noqa: E402
from testcontainers.core.waiting_utils import wait_for_logs  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

SA_PASSWORD = "Str0ng_Passw0rd!"
IMAGE = "mcr.microsoft.com/mssql/server:2022-latest"
TEST_DB = "autocrud_test"
SCHEMA_SQL = os.path.join(os.path.dirname(__file__), "schema.sql")


def _odbc_driver() -> str:
    """Pick an installed SQL Server ODBC driver, preferring 18 over 17."""
    import pyodbc

    drivers = pyodbc.drivers()
    for preferred in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server"):
        if preferred in drivers:
            return preferred
    pytest.skip(f"No SQL Server ODBC driver installed (have: {drivers})")


def _odbc_dsn(driver: str, host: str, port: str, database: str) -> str:
    return (
        f"DRIVER={{{driver}}};SERVER={host},{port};DATABASE={database};"
        f"UID=sa;PWD={SA_PASSWORD};Encrypt=no;TrustServerCertificate=yes;"
    )


def _engine(driver: str, host: str, port: str, database: str):
    return create_engine(
        "mssql+pyodbc:///?odbc_connect=" + urllib.parse.quote_plus(_odbc_dsn(driver, host, port, database))
    )


def _run_sql_script(pyodbc_conn, sql: str) -> None:
    """Execute a multi-batch T-SQL script, splitting on `GO` separators."""
    cursor = pyodbc_conn.cursor()
    for batch in re.split(r"(?im)^[\t ]*GO[\t ]*$", sql):
        if batch.strip():
            cursor.execute(batch)
    cursor.close()


@pytest.fixture(scope="session")
def mssql_engine():
    """Start SQL Server, create the test DB, load schema.sql, yield an Engine."""
    import pyodbc

    # Fast, OS-enforced readiness check first. A half-started Docker daemon can
    # make the docker SDK block indefinitely (e.g. on a Windows named pipe), so
    # probe with a subprocess whose timeout actually kills the child, and skip.
    try:
        subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
            check=True,
        )
    except Exception as e:
        pytest.skip(f"Docker not available (is Docker running?): {e}")

    # Require the image to be present locally and skip otherwise, rather than
    # letting container.start() pull — a pull can hang on DNS instead of failing
    # fast, which would stall the whole suite. One-time prerequisite:
    #   docker pull mcr.microsoft.com/mssql/server:2022-latest
    try:
        subprocess.run(
            ["docker", "image", "inspect", IMAGE],
            capture_output=True, check=True, timeout=15,
        )
    except Exception:
        pytest.skip(f"Image not present locally — run: docker pull {IMAGE}")

    driver = _odbc_driver()

    container = (
        DockerContainer(IMAGE)
        .with_env("ACCEPT_EULA", "Y")
        .with_env("MSSQL_SA_PASSWORD", SA_PASSWORD)
        .with_env("MSSQL_PID", "Developer")
        .with_exposed_ports(1433)
    )
    try:
        container.start()
        wait_for_logs(container, "SQL Server is now ready for client connections", timeout=180)
    except Exception as e:  # Docker not running / image pull failed / startup timeout
        try:
            container.stop()
        except Exception:
            pass
        pytest.skip(f"SQL Server container unavailable (is Docker running?): {e}")

    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(1433)

        master_dsn = _odbc_dsn(driver, host, port, "master")

        # The readiness log fires slightly before connections are accepted —
        # poll until master is reachable.
        last_err = None
        conn = None
        for _ in range(60):
            try:
                conn = pyodbc.connect(master_dsn, autocommit=True, timeout=5)
                break
            except pyodbc.Error as e:
                last_err = e
                time.sleep(2)
        if conn is None:
            pytest.skip(f"SQL Server never became reachable: {last_err}")

        with conn:
            conn.cursor().execute(
                f"IF DB_ID('{TEST_DB}') IS NULL CREATE DATABASE [{TEST_DB}];"
            )

        with open(SCHEMA_SQL, encoding="utf-8") as f:
            schema_sql = f.read()
        db_conn = pyodbc.connect(_odbc_dsn(driver, host, port, TEST_DB), autocommit=True, timeout=15)
        with db_conn:
            _run_sql_script(db_conn, schema_sql)

        engine = _engine(driver, host, port, TEST_DB)
        try:
            yield engine
        finally:
            engine.dispose()
    finally:
        container.stop()


@pytest.fixture(scope="session")
def reflected(mssql_engine):
    """Reflect the comprehensive schema using the production reflect_schemas()."""
    from app import reflection

    saved_engine = reflection.reflection_engine
    saved_schemas = reflection.DB_SCHEMAS
    reflection.reflection_engine = mssql_engine
    reflection.DB_SCHEMAS = ["dbo", "app2"]
    try:
        snapshot = reflection.reflect_schemas()
    finally:
        reflection.reflection_engine = saved_engine
        reflection.DB_SCHEMAS = saved_schemas
    return snapshot


@pytest.fixture
def api(mssql_engine, reflected):
    """TestClient wired to the real database connection + reflected snapshot."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.dependencies import get_db, get_snapshot

    def _override_get_db():
        conn = mssql_engine.connect()
        try:
            with conn.begin():
                yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_snapshot] = lambda: reflected
    app.dependency_overrides[get_db] = _override_get_db
    client = TestClient(app)
    try:
        yield client
    finally:
        app.dependency_overrides.clear()
