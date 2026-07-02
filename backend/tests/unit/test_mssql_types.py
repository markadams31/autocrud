"""
Custom mssql types registered for reflection. Importing app.mssql_types must
register each type by name in the dialect's reflection map, so that
metadata.reflect() yields a real typed column instead of NullType (which is what
lets reflection._classify decide by isinstance — see
test_reflection_classification). Each type's own behaviour — recognition and
column-spec rendering — is asserted here.
"""

import pytest
from sqlalchemy.dialects.mssql.base import MSDialect

from app import mssql_types
from app.mssql_types import GEOGRAPHY, GEOMETRY, HIERARCHYID, JSON, VECTOR


# type name in sys.types → registered SQLAlchemy type
_REGISTERED = {
    "vector": VECTOR,
    "geometry": GEOMETRY,
    "geography": GEOGRAPHY,
    "hierarchyid": HIERARCHYID,
    "json": JSON,
}


@pytest.mark.parametrize("name,typ", list(_REGISTERED.items()))
def test_type_registered_in_dialect_reflection_map(name, typ):
    # metadata.reflect() looks a column's type name up here; without the entry it
    # warns ("Did not recognize type '<name>'") and falls back to NullType.
    assert MSDialect.ischema_names.get(name) is typ


def test_types_render_a_column_spec():
    # str(col.type) drives the metadata `sql_type`, so it must read sensibly
    # rather than the "NULL" an unrecognised type would produce.
    assert str(VECTOR()) == "VECTOR"
    assert str(VECTOR(1536)) == "VECTOR(1536)"
    assert str(GEOMETRY()) == "GEOMETRY"
    assert str(GEOGRAPHY()) == "GEOGRAPHY"
    assert str(HIERARCHYID()) == "HIERARCHYID"
    assert str(JSON()) == "JSON"


@pytest.mark.parametrize("name", list(_REGISTERED))
def test_registration_is_idempotent_and_defers_to_upstream(name):
    # setdefault means re-registering never clobbers an existing mapping — so if a
    # future SQLAlchemy ships native support for one of these, the upstream type wins.
    existing = MSDialect.ischema_names[name]
    mssql_types.register()
    assert MSDialect.ischema_names[name] is existing
