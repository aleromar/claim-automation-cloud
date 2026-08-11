"""Build version of the running backend (version-display REQ-1).

deploy-functions.yml stamps the git SHA into build_version.txt inside the
deploy artifact — the workflow is the only place that knows the SHA (no
version file is ever committed). Everywhere else (local dev, CI, e2e) the
file is absent and the reader falls back to DEV_VERSION.
"""

from pathlib import Path

DEV_VERSION = "dev"
BUILD_VERSION_FILE = Path(__file__).with_name("build_version.txt")


def get_build_version(path: Path = BUILD_VERSION_FILE) -> str:
    """Read the stamped version; absent/empty file -> DEV_VERSION.

    Read per call, uncached (V6): health is fetched once per login, and a
    cache would fight the path injection the unit tests rely on.
    """
    try:
        stamped = path.read_text().strip()
    except FileNotFoundError:
        return DEV_VERSION
    return stamped or DEV_VERSION
