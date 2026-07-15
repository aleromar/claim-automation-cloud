---
inclusion: always
---

# Project Structure & Coding Guidelines — Claim Automation (Cloud)

> The authoritative repo layout, packaging, and per-stack conventions. Completes the steering
> trio with [product.md](./product.md) and [tech.md](./tech.md); governed by
> [constitution.md](./constitution.md). Root [CLAUDE.md](../../CLAUDE.md) points here.

## Monorepo layout

```
claim-automation-cloud/
├── CLAUDE.md                 # concise pointer to this steering dir
├── Makefile                  # cross-stack task runner (install/dev/test/lint/e2e)
├── .github/workflows/ci.yml  # backend + frontend + e2e on every PR
├── backend/                  # Python — FastAPI on Azure Functions (uv)
│   ├── pyproject.toml        # uv project, Python 3.12
│   ├── app/main.py           # FastAPI app + routes
│   ├── function_app.py       # AsgiFunctionApp adapter (Azure deploy entry point)
│   ├── pipeline/             # ported pure-logic package (future; parsing/PDF/Trello)
│   └── tests/                # pytest, SEPARATE tree (unit/ integration/ e2e/)
├── frontend/                 # JavaScript/TypeScript — React + Vite (npm)
│   └── src/                  # components + CO-LOCATED *.test.tsx unit tests
└── e2e/                      # Playwright — cross-stack browser tests
```

Frontend rides with the backend for now (see `azure-implementation.md`); splitting it into its
own repo later changes nothing but the deploy wiring.

## Toolchain (why each tool exists)

| Concern | Backend | Frontend |
|---------|---------|----------|
| Language | Python 3.12 | TypeScript |
| Package manager | **uv** (`pyproject.toml` + `uv.lock`) | **npm** (`package.json` + `package-lock.json`) |
| Dev server | **uvicorn** (`app.main:app`) | **Vite** dev server (HMR + `/api` proxy) |
| Build/bundle | n/a (zip artifact) | **Vite** (`vite build` → static files) |
| Unit tests | **pytest** | **Vitest** + Testing Library |
| Lint/format | **ruff** | **ESLint + Prettier** |
| E2E (both) | **Playwright** (real browser, live uvicorn + Vite) | |

- **Vite** = the frontend's dev server *and* production bundler (the JS analogue of uvicorn + a
  build step). Current industry default; Create React App is deprecated. Its dev proxy forwards
  `/api/*` to the backend so the SPA uses a relative URL in every environment.
- **Playwright** = the one end-to-end test that boots both stacks in a real browser and asserts
  the page shows "All good" — closes the gap the mocked unit tests leave.

### Versions & pinning (reproducibility)

Runtimes are pinned; libraries use conservative ranges.

| What | Pin | Where |
|------|-----|-------|
| Python runtime | **3.12** (`>=3.12,<3.13`) | `backend/pyproject.toml`, `.python-version`, CI |
| Node runtime | **22** | CI (`ci.yml`); local should match |
| Python libs | lower-bound `>=` + `uv.lock` for exact resolution | `pyproject.toml` |
| JS libs | caret `^` ranges + `package-lock.json` for exact resolution | `package.json` |

- The **lockfiles** (`uv.lock`, `package-lock.json`) are the source of truth for exact versions;
  CI uses `uv sync` / `npm ci` to install exactly what's locked.
- React is on **19**, Vite **6**, Vitest **3**, ESLint **9** (**flat config** — `eslint.config.js`,
  not legacy `.eslintrc`).

## FastAPI on Azure Functions (the adapter)

Business code is a plain **FastAPI** app (`app/main.py`) — write and test it like any FastAPI
app. The only Functions-specific code is the thin adapter in `function_app.py`:

```python
import azure.functions as func
from app.main import app as fastapi_app

# The Azure Functions runtime plays the role uvicorn plays locally: it feeds every HTTP
# request into the ASGI (FastAPI) app. One catch-all route; FastAPI does all routing.
app = func.AsgiFunctionApp(app=fastapi_app, http_auth_level=func.AuthLevel.ANONYMOUS)
```

- **`ANONYMOUS`** disables Azure's *own* function-key gate on purpose, so our deliberate auth
  (Google-brokered JWT / `Authorization: Bearer`, per tech.md D17/D22) is the single gate —
  not a second Azure key the SPA would have to ship. Health is a liveness probe, correctly
  unauthenticated.
- Because the adapter is trivial + Functions-specific, local dev and the e2e run **uvicorn**;
  the adapter itself is covered by one small unit test. Proving the real `func start` path is
  deferred hardening.

## Testing conventions (TDD-first — constitution Article I)

**Tests are written before implementation. RED → GREEN → REFACTOR.**

Each stack uses its ecosystem's native convention — do not homogenize:

| | Backend (pytest) | Frontend (Vitest) |
|---|---|---|
| Placement | separate `backend/tests/` tree | **co-located** beside source in `src/` |
| Naming | `test_*.py` | `*.test.tsx` |
| Network | in-process (FastAPI `TestClient`), no real sockets | `fetch` mocked |

- **E2E uses no mocks** — real uvicorn + real Vite + real browser. (One documented
  exception: the third-party IdP — see conventions log, 2026-07-14.)
- **E2E runs chromium only** for now (fastest, matches the deploy target's users); add
  Firefox/WebKit projects only if a cross-browser bug appears.
- Three layers, three questions: pytest ("does the endpoint return right?"), Vitest ("does the
  component render right?"), Playwright ("do they actually talk?").

## Naming & style

- **Python:** `snake_case` funcs/vars, `PascalCase` classes, `UPPER_SNAKE` constants; imports
  ordered stdlib → third-party → local; formatted + linted by **ruff**.
- **TypeScript:** `camelCase` vars/funcs, `PascalCase` components/types; one component per file;
  formatted by **Prettier**, linted by **ESLint**.
- **API paths** are prefixed `/api` (matches the SWA `/api` route convention).

### Frontend data-fetching pattern

Components that call the API follow this shape (see `App.tsx`):

- Model the request as an explicit **state machine** — `"loading" | "ok" | "error"` — and render
  a distinct view for each; never leave a blank screen on failure.
- Fetch inside `useEffect` with a **cancellation flag** in the cleanup to avoid setting state
  after unmount (and to stay correct under React `StrictMode`'s double-invoke in dev).
- Treat a response as healthy **only** when both the HTTP status is ok **and** the body matches
  the expected contract (for health: `{"status":"ok"}`). A `200` with an unexpected body is an
  error state, not success — fail visibly.

## Why no Docker yet (and when it arrives)

Production is **serverless** — Azure Functions (code zip) + Static Web Apps (static files), no
container image on the deploy path. The skeleton is two local processes (`uvicorn` + `vite`), so
`make dev` beats docker-compose (constitution: Simplicity first).

**Docker enters** with the first feature that needs the local **Azurite** emulator (Table
Storage: `enabled` flag, metrics, claim history) — per `azure-implementation.md`. At that point we
add `docker-compose.yml` (Azurite) and move integration/e2e to run against `func start`.

## Conventions log

- Co-located frontend tests (`*.test.tsx`) confirmed as the standard (2026-07-10).
- **E2E "no mocks" — one documented exception:** the third-party IdP (Google) is stubbed
  in e2e, since Google blocks automated sign-in; real-Google coverage is a manual smoke
  checklist (see `.specs/auth/spec.md`) (2026-07-14).
- Dev runtime-written secrets live in a **gitignored local secret file** (0600, atomic
  writes) behind the SecretStore abstraction — env vars alone can't accept runtime writes
  (2026-07-14).
- Relative `/api/*` URLs everywhere; Vite proxy in dev, SWA route in prod.
- No secrets in the repo; local read-only config via gitignored `.env`; runtime-written
  secrets via the gitignored local secret file (see 2026-07-14 entry above).
