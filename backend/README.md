# Backend — Claim Automation (Cloud)

FastAPI application deployed as an Azure Function via `AsgiFunctionApp`.

```bash
uv sync                         # install (incl. dev group)
uv run uvicorn app.main:app --port 8000   # local dev server
uv run pytest                   # unit tests
uv run ruff check . && uv run ruff format --check .   # lint/format
```

## Auth setup (local)

The backend **fails fast at startup** if the session signing key is missing:

1. `make seed-dev` (from the repo root) — creates the gitignored `.secrets.json`
   file secret store with a random signing key.
2. `cp .env.example .env` and fill in the non-secret config.
3. For a **real** Google login you also need the OAuth client secret in the
   store: `uv run python -c "from app.secret_store import FileSecretStore;
   FileSecretStore('.secrets.json').set('google-client-secret', '<value>')"`.
   Unit tests and e2e need none of this — they mock/stub Google entirely.

See [.specs/steering/structure.md](../.specs/steering/structure.md) for conventions and
[.specs/auth/spec.md](../.specs/auth/spec.md) for the auth design (incl. the manual
real-Google smoke checklist for deploy time).
