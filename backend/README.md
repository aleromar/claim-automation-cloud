# Backend — Claim Automation (Cloud)

FastAPI application deployed as an Azure Function via `AsgiFunctionApp`.

```bash
uv sync                         # install (incl. dev group)
uv run --env-file .env uvicorn app.main:app --port 8000   # local dev server (or `make dev`, which brings Azurite up too)
make azurite                    # from the repo root — Azurite emulator, required by the integration tests
brew tap azure/functions && brew install azure-functions-core-tools@4   # `func` CLI, required by the Functions-host integration test
uv run pytest                   # unit + integration tests (fails loudly without Azurite or `func` — no skip logic)
uv run ruff check . && uv run ruff format --check .   # lint/format
```

## Auth setup (local)

The backend **fails fast at startup** if the session signing key, the Google
client secret, or `OPERATOR_EMAIL` is missing:

1. `make seed-dev` (from the repo root) — creates the gitignored `.secrets.json`
   file secret store with a random signing key and a **placeholder** client
   secret (enough to boot; real logins need step 3).
2. `cp .env.example .env` and fill in the non-secret config, including
   `OPERATOR_EMAIL` (the single allowed Google account). The **launcher** loads
   this file (`make dev` / the `--env-file` flag above); the app itself reads
   only environment variables, exactly like prod.
3. For a **real** Google login replace the placeholder client secret in the
   store: `uv run python -c "from app.secret_store import GOOGLE_CLIENT_SECRET,
   FileSecretStore; FileSecretStore('.secrets.json').set(GOOGLE_CLIENT_SECRET,
   '<value>')"`. Unit tests and e2e need none of this — they mock/stub Google
   entirely.

See [.specs/steering/structure.md](../.specs/steering/structure.md) for conventions and
[.specs/auth/spec.md](../.specs/auth/spec.md) for the auth design (incl. the manual
real-Google smoke checklist for deploy time).
