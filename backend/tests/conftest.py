"""
conftest.py — shared fixtures and import-time bootstrap for the whole suite.

Importing any app.* module has import-time side effects: config.py reads the
required env vars (and raises if any are missing), and connection.py builds the
reflection engine and a DefaultAzureCredential. We set deterministic dummy
values *before* importing app so the unit and API tiers never touch a real
database or Azure — and so audit-column classification is reproducible.

We assign os.environ explicitly (not setdefault) so the values are independent
of any repo-root .env that config.load_dotenv() might otherwise pick up.
"""

import os

os.environ["DB_SERVER"] = "test.database.windows.net"
os.environ["DB_DATABASE"] = "testdb"
os.environ["DB_SCHEMAS"] = "dbo"
os.environ["DB_AUDIT_COLUMNS"] = "CreatedBy,CreatedDate,ModifiedBy,ModifiedDate"

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, MetaData, Table, create_engine
from sqlalchemy.dialects.mssql import INTEGER, NVARCHAR, TIMESTAMP
from sqlalchemy.pool import StaticPool

from app import reflection
from app.main import app
from app.dependencies import get_db, get_snapshot
from app.reflection import CatalogFacts, ColumnFacts, SchemaSnapshot, TableInfo
from app.routes.admin import database_is_reachable


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def make_table_info(
    table: Table,
    schema: str = "dbo",
    facts: CatalogFacts | None = None,
) -> TableInfo:
    """
    Build a real TableInfo from a SQLAlchemy Table using the production
    reflection internals — so the create/update models, display column, and
    column classification under test are the exact ones the app would build.

    `facts` carries the per-column catalog facts a live reflection would have
    gathered (computed/default/FK flags), keyed (schema, table, column) with
    schema None for these schemaless harness tables. `schema` keys the
    snapshot even though the table itself carries schema=None, so statements
    stay unqualified and run against sqlite.
    """
    return reflection._build_table_info(table, facts or CatalogFacts(), schema=schema)


def make_snapshot(*table_infos: TableInfo) -> SchemaSnapshot:
    return SchemaSnapshot(tables={ti.key: ti for ti in table_infos})


def pk_default_facts(table: Table) -> CatalogFacts:
    """
    Facts marking a hand-built table's single-column PK as carrying a database
    default — the harness equivalent of "the database supplies this value"
    (sqlite autoincrements it), so it is omitted from the Create model exactly
    like a reflected identity/defaulted PK.
    """
    (pk,) = table.primary_key.columns
    return CatalogFacts({(None, table.name, pk.name): ColumnFacts(has_default=True)})


# ---------------------------------------------------------------------------
# A sqlite-backed "dbo.Widget" used by the API/CRUD tier.
#
# The table is defined with generic types (sqlite has no mssql dialect) and a
# schema of None so statements execute against the in-memory DB. The TableInfo
# carries schema="dbo" for routing/snapshot keying. This is faithful enough to
# exercise all of the route logic — scrubbing, pagination, search, validation,
# error mapping — without a real SQL Server. (mssql-specific reflection fidelity
# is covered by the unit tier with mssql types and the integration tier.)
# ---------------------------------------------------------------------------

def _build_widget():
    # mssql dialect types so reflection's type mapping/classification behaves
    # exactly as in production; the types still compile/run on sqlite.
    md = MetaData()
    table = Table(
        "Widget",
        md,
        Column("WidgetID", INTEGER(), primary_key=True, autoincrement=True),
        Column("Name", NVARCHAR(50), nullable=False),             # required
        Column("Quantity", INTEGER(), nullable=True),             # optional int
        Column("Notes", NVARCHAR(2000), nullable=True),           # text
        Column("CreatedBy", NVARCHAR(128), nullable=True),        # audit → DB_OWNED
    )
    return md, table


@pytest.fixture
def widget():
    """
    A live in-memory sqlite 'dbo.Widget' + a TestClient with get_snapshot and
    get_db overridden. StaticPool keeps every connection on the same in-memory
    database so writes persist across requests.

    Yields a namespace: .client .engine .table_info .snapshot
    """
    md, table = _build_widget()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    md.create_all(engine)

    table_info = make_table_info(table, schema="dbo", facts=pk_default_facts(table))
    snapshot = make_snapshot(table_info)

    def _override_get_db():
        conn = engine.connect()
        try:
            with conn.begin():
                yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_snapshot] = lambda: snapshot
    app.dependency_overrides[get_db] = _override_get_db
    # /health pings the real managed-identity engine; in tests there's no Azure
    # SQL, so stub the readiness probe as healthy.
    app.dependency_overrides[database_is_reachable] = lambda: True
    # No `with TestClient(app)`: that would run the lifespan (real reflection).
    client = TestClient(app)
    try:
        yield SimpleNamespace(
            client=client, engine=engine, table_info=table_info, snapshot=snapshot
        )
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def snapshot_only(widget):
    """
    Same snapshot wired up, but get_db left as the real dependency — so a
    request with no EasyAuth token header exercises the genuine 401 path.
    """
    app.dependency_overrides.pop(get_db, None)
    return widget


# ---------------------------------------------------------------------------
# A sqlite-backed "dbo.Doc" WITH a rowversion column, for optimistic-concurrency
# tests. SQLite doesn't auto-maintain a rowversion, so the fixture seeds one row
# with an explicit 8-byte token; tests read it back (hex) from the API and send
# it as If-Match. The route logic under test — the If-Match WHERE clause and the
# 409-vs-404 distinction — is identical to production; a real auto-incrementing
# rowversion is exercised by the integration tier.
# ---------------------------------------------------------------------------

def _build_doc():
    md = MetaData()
    table = Table(
        "Doc",
        md,
        Column("DocID", INTEGER(), primary_key=True, autoincrement=True),
        Column("Title", NVARCHAR(100), nullable=False),
        Column("Body", NVARCHAR(2000), nullable=True),
        Column("RowVersion", TIMESTAMP()),  # rowversion → concurrency token
    )
    return md, table


@pytest.fixture
def versioned():
    """
    A live in-memory sqlite 'dbo.Doc' (rowversion column) + a TestClient with
    get_snapshot and get_db overridden, seeded with one row.

    Yields a namespace: .client .engine .table_info .snapshot .seeded_pk
    .seeded_token (hex of the seeded rowversion).
    """
    md, table = _build_doc()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    md.create_all(engine)

    seeded_token = b"\x00\x00\x00\x00\x00\x00\x00\x01"
    with engine.begin() as conn:
        result = conn.execute(
            table.insert().values(Title="Original", Body="v1", RowVersion=seeded_token)
        )
        seeded_pk = result.inserted_primary_key[0]

    table_info = make_table_info(table, schema="dbo", facts=pk_default_facts(table))
    snapshot = make_snapshot(table_info)

    def _override_get_db():
        conn = engine.connect()
        try:
            with conn.begin():
                yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_snapshot] = lambda: snapshot
    app.dependency_overrides[get_db] = _override_get_db
    client = TestClient(app)
    try:
        yield SimpleNamespace(
            client=client,
            engine=engine,
            table_info=table_info,
            snapshot=snapshot,
            seeded_pk=seeded_pk,
            seeded_token=seeded_token.hex(),
        )
    finally:
        app.dependency_overrides.clear()
