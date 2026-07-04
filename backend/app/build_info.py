"""
build_info.py — Which build is running.

The image bakes its provenance in as environment variables at build time (the
Dockerfile's ARG/ENV block, fed by `az acr build --build-arg` in
.github/workflows/deploy.yml): the commit SHA the image was built from and the
UTC build timestamp. They're read here with safe defaults so a bare local
`docker build` (→ "unknown", the Dockerfile ARG default) or a plain `uvicorn`
run outside a container (→ "dev", these os.getenv defaults) still works.

Surfaced two ways: a one-line startup log (so each exported AppTraces stream
self-identifies which build produced it) and GET /version (which the frontend's
About dialog shows). Optional and self-contained with safe defaults, so — per
config.py's convention — it's read here at point of use, not in Settings.
"""

from __future__ import annotations

import os

# Set in the image by the Dockerfile. Unset outside a container → "dev".
BUILD_SHA: str = os.getenv("APP_BUILD_SHA", "dev")
BUILD_TIME: str = os.getenv("APP_BUILD_TIME", "dev")


def as_dict() -> dict[str, str]:
    """The running build's provenance, as returned by GET /version."""
    return {"sha": BUILD_SHA, "time": BUILD_TIME}
