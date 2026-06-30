# syntax=docker/dockerfile:1
#
# Self-contained image for Auto CRUD: builds the React frontend and installs the
# FastAPI backend, then ships a runtime image that serves both from one origin.
#
# Build context is the repository ROOT (both frontend/ and backend/ are needed):
#   docker build -t <acr-login-server>/backend:latest .
#
# Nothing here depends on a locally-built frontend — the SPA is compiled inside
# the image, so `docker build` is fully reproducible from a clean checkout.

# ── Stage 1: Frontend build ───────────────────────────────────────────────────
FROM node:22-bookworm-slim AS frontend

WORKDIR /app
# Install dependencies first so this layer caches unless the lockfile changes.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Build the SPA. Output is forced to an explicit path (independent of the
# dev-time outDir in vite.config, which points back into the backend tree).
COPY frontend/ ./
RUN npx tsc -b && npx vite build --outDir /frontend-dist --emptyOutDir


# ── Stage 2: Python dependency build ──────────────────────────────────────────
FROM python:3.13-slim-bookworm AS pybuild

# gcc and unixodbc-dev compile pyodbc (a C extension linked against unixODBC).
# Neither is needed at runtime, so they stay in this throwaway stage.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    unixodbc-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY backend/pyproject.toml .

# Stub the package so pip can resolve and install all declared dependencies
# without the full application source. The stub is discarded afterwards.
RUN mkdir -p app && touch app/__init__.py && \
    pip install --no-cache-dir --prefix=/install . && \
    rm -rf app/


# ── Stage 3: Runtime ──────────────────────────────────────────────────────────
FROM python:3.13-slim-bookworm AS runtime

# Microsoft ODBC Driver 18 for SQL Server — required at runtime by pyodbc for
# every database connection. The driver name must match DB_DRIVER in the app
# config ("ODBC Driver 18 for SQL Server").
#
# The signing key is written to the keyring path referenced by `signed-by` in
# the source list below. apt only trusts a `signed-by` repo against that exact
# keyring (keys in /etc/apt/trusted.gpg.d are ignored for it), so the two must
# point at the same file — otherwise apt reports the repo as unsigned.
#
# libgssapi-krb5-2 is named explicitly because msodbcsql18 links
# libgssapi_krb5.so.2 (Kerberos/GSSAPI, used for Entra auth) without declaring it
# as an apt dependency. Otherwise it arrives only as a transitive dep of curl,
# and the `--auto-remove` below strips it — leaving the driver unloadable
# ("Can't open lib ... file not found") even though the .so is present.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg2 ca-certificates \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends \
        msodbcsql18 \
        unixodbc \
        libgssapi-krb5-2 \
    && apt-get purge -y --auto-remove curl gnupg2 \
    && rm -rf /var/lib/apt/lists/*

# Python packages from the build stage — no build tools carried over.
COPY --from=pybuild /install /usr/local

WORKDIR /app
# Backend application code.
COPY backend/app/ app/
# Built SPA, served by FastAPI as a catch-all from app/frontend/dist.
COPY --from=frontend /frontend-dist app/frontend/dist

# Run as a non-root user.
RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
