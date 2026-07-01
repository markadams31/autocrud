"""
Custom mssql types registered for reflection. Importing app.mssql_types must
register VECTOR by name in the dialect's reflection map, so that
metadata.reflect() yields a real typed column instead of NullType (which is what
lets reflection._classify exclude it by isinstance — see
test_reflection_classification). The type's own behaviour — recognition and
column-spec rendering — is asserted here.
"""

from sqlalchemy.dialects.mssql.base import MSDialect

from app import mssql_types
from app.mssql_types import VECTOR


def test_vector_registered_in_dialect_reflection_map():
    # metadata.reflect() looks a column's type name up here; without the entry it
    # warns ("Did not recognize type 'vector'") and falls back to NullType.
    assert MSDialect.ischema_names.get("vector") is VECTOR


def test_vector_renders_a_column_spec():
    # str(col.type) drives the metadata `sql_type`, so it must read sensibly
    # rather than the "NULL" an unrecognised type would produce.
    assert str(VECTOR()) == "VECTOR"
    assert str(VECTOR(1536)) == "VECTOR(1536)"


def test_registration_is_idempotent_and_defers_to_upstream():
    # setdefault means re-registering never clobbers an existing mapping — so if a
    # future SQLAlchemy ships native 'vector' support, the upstream type wins.
    existing = MSDialect.ischema_names["vector"]
    mssql_types.register()
    assert MSDialect.ischema_names["vector"] is existing
