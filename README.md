# Claim Automation (Cloud)

Automates insurance-claim intake end to end: a scheduled worker polls a Gmail
inbox for unread claim emails, parses each one into structured claim data,
generates a letterhead PDF, creates or updates the matching Trello card,
records the result, and relabels the email. A web dashboard lets the single
operator sign in with Google, configure Trello credentials, watch metrics,
and switch the worker on or off.

It is a cloud re-implementation of an earlier laptop app: same pipeline
logic, but hosted on Azure so nothing depends on a laptop being awake, and
controlled through a browser instead of a local GUI.

## How it works

- **Worker on/off is a flag, not infrastructure.** A timer fires every
  30 minutes regardless; the worker's first act is to read an `enabled` flag
  from the state store. Off → heartbeat and exit in milliseconds. On → run
  the full pipeline. The dashboard toggle just flips the flag.
- **One Google sign-in does double duty.** The operator's login consent also
  grants Gmail access; the backend brokers OAuth, checks a single-email
  allowlist, and issues a signed session JWT. A "Reconnect Gmail" state
  appears when the refresh token goes stale.

## Architecture

| Piece | Stack | Runs on |
|-------|-------|---------|
| `backend/` | Python 3.12, FastAPI (uv, pytest, ruff) | Azure Functions, via a thin `AsgiFunctionApp` adapter |
| `frontend/` | React 19 + TypeScript, Vite (Vitest, ESLint/Prettier) | Azure Static Web Apps |
| `e2e/` | Playwright (Chromium) | CI + locally, against real uvicorn + Vite |
| State | Azure Table Storage (worker flag, heartbeat, metrics, claim ledger) | Azurite emulator locally |

The backend is split in three layers: `core/` (config, secret store, state
store — shared kernel), `pipeline/` (the workload: parsing, PDF generation,
Gmail/Trello clients), and `app/` (the control plane: worker wake, dashboard
API, operator auth). Imports flow one way: `app → pipeline → core`.

The frontend calls the API through relative `/api/*` paths — Vite proxies
them in dev; the production build bakes in the Function App origin via
`VITE_API_BASE_URL`.

## Prerequisites

- Python 3.12 + [uv](https://docs.astral.sh/uv/)
- Node 22 + npm
- Docker (only for the local Azurite storage emulator)
- Azure Functions Core Tools v4 (`brew tap azure/functions && brew install
  azure-functions-core-tools@4`) — required by the backend integration tests

## Quick start

```bash
make install     # uv sync + npm ci for frontend and e2e (+ Playwright chromium)
make seed-dev    # one-time: seed the local secret store (signing key + placeholder Google secret)
cp backend/.env.example backend/.env   # then fill in the non-secret config
make dev         # Azurite (Docker) + uvicorn :8000 + Vite :5173
```

Real Google logins need one extra step (replacing the placeholder client
secret) — see [backend/README.md](backend/README.md).

## Everyday commands

| Command | What it does |
|---------|--------------|
| `make dev` | Run backend + frontend together (starts Azurite first) |
| `make test` | Backend pytest (unit + integration vs Azurite) + frontend Vitest |
| `make lint` | ruff + ESLint/Prettier, both stacks |
| `make e2e` | Playwright cross-stack suite (boots both stacks itself; Google is stubbed) |
| `make e2e-live` | Live smoke suite against a real Gmail dev account + Trello test board (needs `e2e/.env.live`) |
| `make azurite` | Start/restart just the Azurite container (`docker rm -f claim-azurite` to reset state) |

Tests are written before implementation (TDD) throughout; CI runs the
backend, frontend, and e2e suites on every PR.

## Going deeper

- [backend/README.md](backend/README.md) — backend commands and local auth setup
- [frontend/README.md](frontend/README.md) — a guided tour of the frontend for readers new to TypeScript

Secrets never live in the repo: local config sits in gitignored `.env`
files, runtime-written secrets in a gitignored local secret store, and CI
uses GitHub Actions secrets / OIDC.
