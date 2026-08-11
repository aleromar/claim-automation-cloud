"""Build version of the running backend (version-display REQ-1).

deploy-functions.yml stamps the git SHA into build_version.txt inside the
deploy artifact — the workflow is the only place that knows the SHA (no
version file is ever committed). Everywhere else (local dev, CI, e2e) the
file is absent and the reader falls back to DEV_VERSION.
"""

from functools import cache
from pathlib import Path

DEV_VERSION = "dev"
BUILD_VERSION_FILE = Path(__file__).with_name("build_version.txt")


@cache
def get_build_version(path: Path = BUILD_VERSION_FILE) -> str:
    """Read the stamped version; absent/empty/unreadable file -> DEV_VERSION.

    Cached per path (V6, amended 2026-08-11): the stamp is immutable for the
    process lifetime (run-from-package mount; a deploy restarts the workers),
    and the cache key is the path, so the unit tests' injected tmp paths never
    collide with the production entry.
    """
    try:
        return path.read_text().strip() or DEV_VERSION
    except OSError:
        return DEV_VERSION
