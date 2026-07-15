"""SecretStore abstraction (REQ-5): one interface, pluggable backends.

Local dev = gitignored JSON file (0600, atomic replace) — a file rather than env
vars because some secrets are runtime-written (Gmail refresh token). Production =
Key Vault, implemented in the infra feature.
"""

import json
import os
from pathlib import Path
from typing import Final, Literal, Protocol

from app.config import Settings

SESSION_SIGNING_KEY: Final = "session-signing-key"
GOOGLE_CLIENT_SECRET: Final = "google-client-secret"
GMAIL_REFRESH_TOKEN: Final = "gmail-refresh-token"

# Closed set of secret names: a typo becomes a type error, not a silent None.
SecretName = Literal["session-signing-key", "google-client-secret", "gmail-refresh-token"]


class SecretStore(Protocol):
    def get(self, name: SecretName) -> str | None: ...

    def set(self, name: SecretName, value: str) -> None: ...


class FileSecretStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def get(self, name: str) -> str | None:
        return self._read().get(name)

    def set(self, name: str, value: str) -> None:
        secrets = self._read()
        secrets[name] = value
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(secrets, f)
        os.replace(tmp, self._path)

    def _read(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text())


def require_secret(store: SecretStore, name: SecretName) -> str:
    """Return the secret or fail loudly — a required secret must never degrade to ""."""
    value = store.get(name)
    if not value:
        raise RuntimeError(
            f"{name} missing from the secret store — seed it before starting "
            "(dev: make seed-dev, see backend/README; prod: infra pipeline)"
        )
    return value


def create_secret_store(settings: Settings) -> SecretStore:
    if settings.secret_store_backend == "file":
        return FileSecretStore(settings.secret_store_file_path)
    # "keyvault" is the only other value the Settings type admits.
    raise NotImplementedError(
        "keyvault secret store is not implemented in this slice (arrives with the infra feature)"
    )
