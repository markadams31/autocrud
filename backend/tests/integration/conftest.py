"""
Tier-3 integration fixtures — a real SQL Server in Docker via Testcontainers.

Everything here is gated:
  - skipped if `testcontainers` isn't installed,
  - skipped if no `ODBC Driver 1x for SQL Server` is available locally,
  - skipped if Docker can't start the container.

So a plain `pytest` run still works without Docker; these light up only when
the environment can support them. In CI the calculus inverts: this tier is the
project's main gate (the reflection golden lives here), so a run that silently
skips is a green build that verified nothing. CI sets CI_REQUIRE_INTEGRATION=1,
which turns every environment-gate skip into a hard failure — the 2022→2025
image rename slipped through exactly this crack once.

The container is driven directly through the generic DockerContainer + pyodbc
(no pymssql dependency for readiness); the app under test runs on mssql-python.
"""

import os
import re
import subprocess
import time

import pytest


def _skip(reason: str):
    """Skip locally; fail in CI, where a skipped tier would be a silent no-op."""
    if os.environ.get("CI_REQUIRE_INTEGRATION"):
        pytest.fail(f"integration tier is required in CI but could not run: {reason}")
    pytest.skip(reason)


# Skip the whole tier if the optional dependency isn't present.
pytest.importorskip("testcontainers", reason="testcontainers not installed (uv sync --extra test)")

from testcontainers.core.container import DockerContainer  # noqa: E402
from testcontainers.core.wait_strategies import LogMessageWaitStrategy  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

SA_PASSWORD = "Str0ng_Passw0rd!"
# The least-privilege reflection login created by schema.sql: VIEW DEFINITION
# only, no data-reading role. The reflection matrix re-runs under it to prove
# parity with sa (see the `snapshot` fixture below).
VDONLY_USER = "reflect_vdonly"
VDONLY_PASSWORD = "Int3gration_VD!only"
# SQL Server 2025: the schema fixture exercises the native json and vector types
# introduced there (see schema.sql), which earlier images reject at CREATE TABLE.
IMAGE = "mcr.microsoft.com/mssql/server:2025-latest"
TEST_DB = "autocrud_test"
SCHEMA_SQL = os.path.join(os.path.dirname(__file__), "schema.sql")


def _odbc_driver() -> str:
    """Pick an installed SQL Server ODBC driver, preferring 18 over 17."""
    import pyodbc

    drivers = pyodbc.drivers()
    for preferred in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server"):
        if preferred in drivers:
            return preferred
    _skip(f"No SQL Server ODBC driver installed (have: {drivers})")


def _odbc_dsn(
    driver: str, host: str, port: str, database: str,
    uid: str = "sa", pwd: str = SA_PASSWORD,
) -> str:
    return (
        f"DRIVER={{{driver}}};SERVER={host},{port};DATABASE={database};"
        f"UID={uid};PWD={pwd};Encrypt=no;TrustServerCertificate=yes;"
    )


def _engine(driver: str, host: str, port: str, database: str, uid: str = "sa", pwd: str = SA_PASSWORD):
    """
    An Engine on the app's production driver (mssql-python), so every
    integration test exercises the same DBAPI the app ships with. pyodbc is
    used only to bootstrap the container (readiness poll + schema load); the
    `driver` argument names the ODBC driver for that bootstrap DSN and is
    unused here — mssql-python bundles its own driver.
    """
    conn_str = (
        f"Server={host},{port};Database={database};"
        f"UID={uid};PWD={pwd};Encrypt=no;TrustServerCertificate=yes;"
    )

    def _connect():
        import mssql_python

        return mssql_python.connect(conn_str)

    return create_engine("mssql+mssqlpython://", creator=_connect)


def _run_sql_script(pyodbc_conn, sql: str) -> None:
    """Execute a multi-batch T-SQL script, splitting on `GO` separators."""
    cursor = pyodbc_conn.cursor()
    for batch in re.split(r"(?im)^[\t ]*GO[\t ]*$", sql):
        if batch.strip():
            cursor.execute(batch)
    cursor.close()


@pytest.fixture(scope="session")
def mssql_server():
    """Start SQL Server, create the test DB, load schema.sql, yield (driver, host, port)."""
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
        _skip(f"Docker not available (is Docker running?): {e}")

    # Require the image to be present locally and skip otherwise, rather than
    # letting container.start() pull — a pull can hang on DNS instead of failing
    # fast, which would stall the whole suite. One-time prerequisite:
    #   docker pull mcr.microsoft.com/mssql/server:2025-latest
    try:
        subprocess.run(
            ["docker", "image", "inspect", IMAGE],
            capture_output=True, check=True, timeout=15,
        )
    except Exception:
        _skip(f"Image not present locally — run: docker pull {IMAGE}")

    driver = _odbc_driver()

    container = (
        DockerContainer(IMAGE)
        .with_env("ACCEPT_EULA", "Y")
        .with_env("MSSQL_SA_PASSWORD", SA_PASSWORD)
        .with_env("MSSQL_PID", "Developer")
        .with_exposed_ports(1433)
        # Block on .start() until SQL Server logs readiness — the structured wait
        # strategy. (The old wait_for_logs(container, <string>) form is deprecated
        # in testcontainers 4.x.)
        .waiting_for(
            LogMessageWaitStrategy(
                "SQL Server is now ready for client connections"
            ).with_startup_timeout(180)
        )
    )
    try:
        container.start()
    except Exception as e:  # Docker not running / image pull failed / startup timeout
        try:
            container.stop()
        except Exception:
            pass
        _skip(f"SQL Server container unavailable (is Docker running?): {e}")

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
            _skip(f"SQL Server never became reachable: {last_err}")

        with conn:
            conn.cursor().execute(
                f"IF DB_ID('{TEST_DB}') IS NULL CREATE DATABASE [{TEST_DB}];"
            )

        with open(SCHEMA_SQL, encoding="utf-8") as f:
            schema_sql = f.read()
        db_conn = pyodbc.connect(_odbc_dsn(driver, host, port, TEST_DB), autocommit=True, timeout=15)
        with db_conn:
            _run_sql_script(db_conn, schema_sql)

        yield driver, host, port
    finally:
        container.stop()


@pytest.fixture(scope="session")
def mssql_engine(mssql_server):
    """sa-authenticated Engine against the test database."""
    driver, host, port = mssql_server
    engine = _engine(driver, host, port, TEST_DB)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def vdonly_engine(mssql_server):
    """
    Engine authenticated as the least-privilege reflection login: VIEW DEFINITION
    only, no data access. This is the identity the production reflection engine
    should run as — reflection reads metadata, never rows.
    """
    driver, host, port = mssql_server
    engine = _engine(driver, host, port, TEST_DB, uid=VDONLY_USER, pwd=VDONLY_PASSWORD)
    try:
        yield engine
    finally:
        engine.dispose()


def _reflect_schemas_with(engine):
    """Run the production reflect_schemas() against the given engine."""
    from app import reflection

    saved_engine = reflection.reflection_engine
    saved_schemas = reflection.DB_SCHEMAS
    reflection.reflection_engine = engine
    reflection.DB_SCHEMAS = ["dbo", "app2"]
    try:
        return reflection.reflect_schemas()
    finally:
        reflection.reflection_engine = saved_engine
        reflection.DB_SCHEMAS = saved_schemas


@pytest.fixture(scope="session")
def reflected(mssql_engine):
    """Snapshot reflected as sa — the baseline, also used by the CRUD round-trips."""
    return _reflect_schemas_with(mssql_engine)


@pytest.fixture(scope="session")
def reflected_vdonly(vdonly_engine):
    """Snapshot reflected as the VIEW-DEFINITION-only login."""
    return _reflect_schemas_with(vdonly_engine)


@pytest.fixture(scope="session", params=["sa", "vdonly"])
def snapshot(request, reflected, reflected_vdonly):
    """
    The reflection matrix runs once per identity: sa and the least-privilege
    VIEW-DEFINITION-only login. Both must produce identical classification —
    that parity is the module's core promise, asserted per-scenario here and
    wholesale in test_reflection_golden.
    """
    return reflected if request.param == "sa" else reflected_vdonly


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
