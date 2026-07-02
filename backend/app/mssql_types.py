"""
mssql_types.py — Custom SQLAlchemy types for SQL Server types the installed
mssql dialect doesn't yet recognise, registered into the dialect's reflection
map so metadata.reflect() produces a real typed column instead of NullType.

Why register instead of reading sys.types
-----------------------------------------
When reflection meets a type name absent from the dialect's `ischema_names`, it
warns ("Did not recognize type 'vector'") and falls back to NullType — an
untyped column that can't be isinstance-checked, whose str(col.type) misreports
as "NULL", and (for a writable type) can't marshal values. Registering a type
here teaches reflection the name once, so the column then flows through the SAME
type-driven machinery every built-in type uses: classification by isinstance
(reflection._classify), str(col.type) for metadata, and value binding on write.

That keeps the workaround for an unknown *type* confined to one type definition,
rather than a catalog read threaded through classification. It's the right tool
for the "new type" gap specifically; the privilege-gated *flag* gaps
(is_computed / generated_always / default / FK) still need their sys.* reads —
reflection structurally can't see those — see reflection._column_flags.

Two orthogonal facts a registered type still can't express, handled elsewhere:

  - Writability. VECTOR / GEOMETRY / GEOGRAPHY are structured-but-opaque and are
    kept out of write payloads (reflection._EXCLUDED_WRITE_TYPES); HIERARCHYID
    and JSON are plain strings that round-trip, so they stay EDITABLE.
  - Fetchability. The CLR types (HIERARCHYID/GEOMETRY/GEOGRAPHY) can't be
    materialised in a result row by pyodbc (ODBC type -151), independent of how
    SQLAlchemy names them; reflection._UNFETCHABLE_TYPES flags them so the read
    path CASTs them to text (routes/crud._read_columns).

Registration is idempotent and uses setdefault, so if a future SQLAlchemy ships
native support for one of these names, the upstream mapping wins. `register()`
runs on import; reflection imports the type from this module, so the entry is in
place before the first metadata.reflect().
"""
from __future__ import annotations

from sqlalchemy.dialects.mssql.base import MSDialect
from sqlalchemy.types import UserDefinedType


class VECTOR(UserDefinedType):
    """
    The Azure SQL / SQL Server 2025 VECTOR type — a fixed-dimension float32
    embedding, exposed by the engine as a JSON array.

    Read-only in this app: a raw embedding isn't meaningfully hand-editable
    through a generic CRUD layer, so it only needs to be *recognisable*, not
    writable — reflection._classify excludes it by isinstance, exactly like
    binary/XML. The optional dimension isn't captured on reflection (the dialect
    instantiates an unrecognised type with no args); it isn't needed for
    read-only exclusion, and get_col_spec still renders it when set.
    """

    cache_ok = True

    def __init__(self, dim: int | None = None):
        self.dim = dim

    def get_col_spec(self, **kw) -> str:
        return "VECTOR" if self.dim is None else f"VECTOR({self.dim})"


class GEOMETRY(UserDefinedType):
    """
    The SQL Server GEOMETRY spatial CLR type. Structured-but-opaque, so it's
    read-only (excluded from writes like XML). pyodbc can't fetch it raw
    (ODBC -151); the read path CASTs it to its WKT text form.
    """

    cache_ok = True

    def get_col_spec(self, **kw) -> str:
        return "GEOMETRY"


class GEOGRAPHY(UserDefinedType):
    """The SQL Server GEOGRAPHY spatial CLR type. Read-only; see GEOMETRY."""

    cache_ok = True

    def get_col_spec(self, **kw) -> str:
        return "GEOGRAPHY"


class HIERARCHYID(UserDefinedType):
    """
    The SQL Server HIERARCHYID CLR type — a tree path such as "/1/2/".

    A path is a plain string that round-trips (SQL Server converts a string to
    hierarchyid on write and .ToString()s it back), so it stays EDITABLE. But,
    like the spatial CLR types, pyodbc can't fetch it raw (ODBC -151), so the
    read path CASTs it to its path string.
    """

    cache_ok = True

    def get_col_spec(self, **kw) -> str:
        return "HIERARCHYID"


class JSON(UserDefinedType):
    """
    The SQL Server 2025 native JSON type.

    A JSON document is a string that round-trips (the engine accepts a JSON
    string on write and returns one on read), so it stays EDITABLE and is
    fetchable. Deliberately NOT the dialect's mssql.JSON (which would map to a
    Python dict and reject a top-level array/scalar); reflection maps this to a
    plain str, letting the client send any valid JSON document and the database
    validate it.
    """

    cache_ok = True

    def get_col_spec(self, **kw) -> str:
        return "JSON"


def register() -> None:
    """
    Teach the mssql dialect's reflection about these type names. Idempotent, and
    setdefault so a future built-in mapping wins over ours if it ever lands.
    """
    MSDialect.ischema_names.setdefault("vector", VECTOR)
    MSDialect.ischema_names.setdefault("geometry", GEOMETRY)
    MSDialect.ischema_names.setdefault("geography", GEOGRAPHY)
    MSDialect.ischema_names.setdefault("hierarchyid", HIERARCHYID)
    MSDialect.ischema_names.setdefault("json", JSON)


register()
