"""
Whole-snapshot pins for the reflection contract, against real SQL Server.

Two tests, two purposes:

  Parity   The snapshot reflected as the least-privilege login (VIEW DEFINITION
           only, no data access) must be field-for-field identical to the one
           reflected as sa. This is the module's privilege promise stated once,
           wholesale — the matrix asserts it scenario by scenario.

  Golden   The sa snapshot serialized field-for-field must match the checked-in
           golden_snapshot.json. This freezes the full reflection contract so a
           refactor can prove itself behaviour-preserving: any change to
           classification, typing, requiredness, FKs, or model shapes shows up
           as a readable diff. Regenerate deliberately after an intended
           behaviour change with:

               UPDATE_GOLDEN=1 uv run pytest tests/integration/test_reflection_golden.py

Docker-gated (see integration/conftest.py).
"""

import json
import os

import pytest

pytestmark = pytest.mark.integration

GOLDEN_PATH = os.path.join(os.path.dirname(__file__), "golden_snapshot.json")


def _serialize(snapshot) -> dict:
    """Every consumer-visible fact in the snapshot, as stable JSON-able data."""
    out: dict = {}
    for (schema, name), t in sorted(snapshot.tables.items()):
        out[f"{schema}.{name}"] = {
            "primary_key": list(t.primary_key),
            "display_column": t.display_column,
            "concurrency_token": t.concurrency_token,
            "columns": [
                {
                    "name": c.name,
                    "kind": c.kind.name,
                    "python_type": c.python_type.__name__,
                    "sql_type": c.sql_type,
                    "nullable": c.nullable,
                    "is_primary_key": c.is_primary_key,
                    "is_audit": c.is_audit,
                    "required_on_create": c.required_on_create,
                    "searchable": c.searchable,
                    "filterable": c.filterable,
                    "read_as_text": c.read_as_text,
                    "comment": c.comment,
                    "max_length": c.max_length,
                    "precision": c.precision,
                    "scale": c.scale,
                    "foreign_key": list(c.foreign_key) if c.foreign_key else None,
                }
                for c in t.columns
            ],
            # The generated Pydantic models are part of the contract too: which
            # fields exist and which are required.
            "create_model": {
                fname: f.is_required()
                for fname, f in sorted(t.create_model.model_fields.items())
            },
            "update_model": sorted(t.update_model.model_fields),
        }
    return out


def test_vdonly_reflects_identically_to_sa(reflected, reflected_vdonly):
    sa, vd = _serialize(reflected), _serialize(reflected_vdonly)
    assert sorted(sa) == sorted(vd), "table sets differ between sa and VD-only"
    for key in sa:
        assert sa[key] == vd[key], f"reflection differs under VD-only for {key}"


def test_snapshot_matches_golden(reflected):
    actual = _serialize(reflected)

    if os.environ.get("UPDATE_GOLDEN"):
        with open(GOLDEN_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(actual, f, indent=2, sort_keys=True)
            f.write("\n")
        pytest.skip(f"golden snapshot rewritten: {GOLDEN_PATH}")

    assert os.path.exists(GOLDEN_PATH), (
        "golden_snapshot.json is missing — generate it with UPDATE_GOLDEN=1"
    )
    with open(GOLDEN_PATH, encoding="utf-8") as f:
        golden = json.load(f)

    assert sorted(actual) == sorted(golden), "table set changed vs golden"
    for key in actual:
        assert actual[key] == golden[key], (
            f"reflection contract changed for {key} — if intended, regenerate "
            f"with UPDATE_GOLDEN=1"
        )
