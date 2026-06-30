"""
config.py — The required environment configuration, read once, in one place.

Modules import their required settings from here rather than reading os.environ
directly. (A few optional, self-contained logging/telemetry knobs with safe
defaults — LOG_LEVEL, LOG_USER_IDENTITY_SALT, and the Application Insights
settings — are read at their point of use instead.) Centralising the required
settings buys two things:

  1. One failure mode instead of N. A missing variable surfaces once, at
     startup, with every problem listed together — not as a different
     ImportError or KeyError depending on which module imported first.

  2. One place to know what the app needs. A new developer (or a deploy
     checklist) can read this file top to bottom and see every required
     setting, rather than grepping for os.environ across the codebase.

The settings themselves are a pydantic-settings `BaseSettings` model: types,
required-ness, and parsing are declared on the model and validated by Pydantic
(already a core dependency). `_build_settings()` turns Pydantic's ValidationError
into one friendly RuntimeError so a misconfigured deployment fails fast and
loudly rather than failing on the first request.
"""

from __future__ import annotations

from typing import Annotated

from dotenv import load_dotenv
from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Load a .env (searching upward from this file) into the process environment
# before Settings reads it, so launching from the repo root or backend/ both
# work. Existing environment variables win — load_dotenv does not override them.
load_dotenv()


class Settings(BaseSettings):
    """Environment configuration, validated on startup."""

    # Env var names are matched case-insensitively (DB_SERVER → db_server).
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    db_server: str = Field(min_length=1)
    db_database: str = Field(min_length=1)
    db_driver: str = Field(min_length=1)

    # How the signed-in user's identity appears in logs (access log + the CRUD
    # audit lines). The user's email is PII; in a shared/production deployment
    # you may not want it in plaintext in Log Analytics.
    #   "email" — log the UPN/email as-is (default; best for support).
    #   "hash"  — log a short, stable pseudonym so requests by the same user
    #             still correlate, without storing the address. Salt with
    #             LOG_USER_IDENTITY_SALT to resist reversal of known addresses.
    #   "none"  — never log user identity ("-").
    log_user_identity: str = Field(default="email")

    @field_validator("log_user_identity", mode="before")
    @classmethod
    def _normalise_user_identity(cls, value: object) -> object:
        if isinstance(value, str):
            v = value.strip().lower()
            if v not in {"email", "hash", "none"}:
                raise ValueError("must be one of: email, hash, none")
            return v
        return value

    # Hard ceiling on how many rows a single bulk operation may touch. Bulk
    # writes run inside one transaction (all-or-nothing); this cap keeps that
    # transaction from holding locks / growing the log without bound on a
    # generic tool pointed at arbitrary databases. Raise it if you routinely
    # need larger atomic batches.
    bulk_max_rows: int = Field(default=1000, ge=1)

    # NoDecode stops pydantic-settings from trying to JSON-parse these env
    # values; the validators below split the comma-separated string instead.
    db_schemas: Annotated[list[str], NoDecode] = Field(min_length=1)
    db_audit_columns: Annotated[set[str], NoDecode] = Field(default_factory=set)

    @field_validator("db_schemas", mode="before")
    @classmethod
    def _parse_schemas(cls, value: object) -> object:
        if isinstance(value, str):
            return [s.strip() for s in value.split(",") if s.strip()]
        return value

    @field_validator("db_audit_columns", mode="before")
    @classmethod
    def _parse_audit_columns(cls, value: object) -> object:
        # Matched case-insensitively downstream (SQL Server identifiers are
        # case-insensitive), so normalise to lowercase here.
        if isinstance(value, str):
            return {c.strip().lower() for c in value.split(",") if c.strip()}
        return value


def _build_settings() -> Settings:
    """
    Instantiate Settings, converting Pydantic's ValidationError into a single
    RuntimeError that lists every problem at once — so a misconfigured
    deployment fails fast on startup with a clear, actionable message.
    """
    try:
        return Settings()
    except ValidationError as exc:
        problems = []
        for err in exc.errors():
            name = str(err["loc"][0]).upper() if err["loc"] else "(config)"
            reason = "is required" if err["type"] == "missing" else err["msg"]
            problems.append(f"{name} {reason}")
        raise RuntimeError(
            "Invalid environment configuration:\n  - "
            + "\n  - ".join(problems)
            + "\nSee .env.example."
        ) from None


_settings = _build_settings()

# Module-level names every other module imports. Keeping these as plain values
# (not attribute access on a settings object) preserves the existing import
# surface across the app.
DB_SERVER:        str       = _settings.db_server
DB_DATABASE:      str       = _settings.db_database
DB_DRIVER:        str       = _settings.db_driver
DB_SCHEMAS:       list[str] = _settings.db_schemas
DB_AUDIT_COLUMNS: set[str]  = _settings.db_audit_columns
BULK_MAX_ROWS:    int       = _settings.bulk_max_rows
LOG_USER_IDENTITY: str      = _settings.log_user_identity
