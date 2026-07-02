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
# Every dependency ships as a wheel (mssql-python bundles its own SQL Server
# driver), so no compilers or ODBC headers are needed.
#
# Installed with uv FROM THE LOCKFILE, not resolved from pyproject ranges: the
# image gets exactly the versions the test suites ran against (uv export
# --frozen fails the build if uv.lock is stale), with the lock's hashes
# verified at install time. Resolving ranges here instead would let a new
# upstream release — e.g. SQLAlchemy 2.1 final inside the >=2.1.0b3,<2.2 pin —
# reach a deployed image before the lockfile (and CI) ever saw it. Dependency
# bumps therefore always go lockfile-first. uv is also ~10% faster here, but
# determinism is the point.
FROM python:3.13-slim-bookworm AS pybuild
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /usr/local/bin/uv

WORKDIR /build
COPY backend/pyproject.toml backend/uv.lock ./

# --no-emit-project: dependencies only, so no stub package is needed and the
# layer caches until the lockfile itself changes.
RUN uv export --frozen --no-emit-project -o requirements.txt && \
    uv pip install --no-cache --require-hashes --prefix=/install -r requirements.txt


# ── Stage 3: Runtime ──────────────────────────────────────────────────────────
FROM python:3.13-slim-bookworm AS runtime

# No database driver installation: mssql-python bundles the SQL Server driver
# (a vendored msodbcsql-18.6 per distro) inside its wheel, replacing the
# msodbcsql18 + unixODBC apt stack (and its signing-key/EULA ceremony) that
# pyodbc required here previously. Two shared libraries the bundled driver
# links but the slim base image lacks (found via ldd against the wheel's .so):
#   libltdl7          libtool's dlopen wrapper (came with the old msodbcsql18)
#   libgssapi-krb5-2  Kerberos/GSSAPI, used in the Entra auth path
RUN apt-get update \
    && apt-get install -y --no-install-recommends libltdl7 libgssapi-krb5-2 \
    && rm -rf /var/lib/apt/lists/*

# Python packages from the build stage — no build tools carried over.
COPY --from=pybuild /install /usr/local

WORKDIR /app
# Backend application code.
COPY backend/app/ app/
# Built SPA, served by FastAPI as a catch-all from app/frontend/dist.
COPY --from=frontend /frontend-dist app/frontend/dist

# Build provenance — passed by CI (`az acr build --build-arg`, see
# .github/workflows/deploy.yml) and baked in as env vars so the running app can
# report exactly which commit it is and when it was built: a startup log line and
# GET /version (read in app.build_info). Declared late so changing them doesn't
# invalidate the heavy layers above.
#
# The build runs in ACR regardless of who starts it; only whether --build-arg is
# supplied matters. Without the args the image reports "unknown" (a useful
# "not built by CI" signal). For a manual build WITH provenance, pass them too:
#   az acr build -r <acr> -t autocrud:$(git rev-parse --short HEAD) \
#     --build-arg GIT_SHA=$(git rev-parse --short HEAD) \
#     --build-arg BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ) .
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ENV APP_BUILD_SHA=$GIT_SHA \
    APP_BUILD_TIME=$BUILD_TIME

# Run as a non-root user.
RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
