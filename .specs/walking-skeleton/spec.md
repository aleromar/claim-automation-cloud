# Spec (Lite): Walking Skeleton — Monorepo Scaffold + Health Check

> **Created:** 2026-07-10
> **Status:** Draft
> **Mode:** Lite (single-file: requirements + design + tasks)
> **Steering:** [constitution](../steering/constitution.md) · [product](../steering/product.md) · [tech](../steering/tech.md)
> **Authors:** Claude + Mr Rodriguez

## Overview

Establish the multi-technology monorepo (Python backend + JavaScript/TypeScript
frontend) with correct structure, packaging, tooling, and a TDD test harness — **before any
product feature exists**. The vertical slice that proves the wiring is a **health check**:
a FastAPI `/api/health` endpoint (deployed later as an Azure Function via `AsgiFunctionApp`)
and a React/Vite/TS page that calls it and renders **"All good"** on success or an error
state otherwise.

This is the *walking skeleton*: the thinnest end-to-end path that exercises both stacks,
their tests, and CI, so that every later feature (auth, worker, dashboard) lands on a proven
foundation. It intentionally implements **no** claim-automation business logic.

## Decisions (from grill-me, all resolved)

| # | Decision | Value |
|---|----------|-------|
| S1 | Backend HTTP | **FastAPI** app, mounted on Azure Functions via `AsgiFunctionApp` (thin `function_app.py` adapter) |
| S2 | Frontend stack | **React + Vite + TypeScript** |
| S3 | Python packaging | **uv** (`pyproject.toml` + `uv.lock`), Python **3.12** (matches `../claim_automation`) |
| S4 | Test depth | **Unit both sides + one Playwright e2e** |
| S5 | E2E backend target | **uvicorn** (FastAPI direct) + Vite dev server; adapter covered by its own unit test |
| S6 | CI | **GitHub Actions `ci.yml` now** — ruff + pytest, ESLint/Prettier + Vitest, Playwright e2e |
| S7 | Guidelines home | **`.specs/steering/structure.md`** (full detail) + root **`CLAUDE.md`** (concise pointer) |
| S8 | Task runner | Root **Makefile** (`make dev/test/lint/e2e/install`) — org pattern |
| S9 | Lint/format | Python → **ruff**; TS → **ESLint + Prettier** |
| S10 | Local FE↔BE wiring | **Vite dev proxy** `/api` → backend `http://localhost:8000` (mirrors SWA `/api` convention) |
| S11 | Health contract | **Liveness only** — `GET /api/health` → `200 {"status":"ok"}` (no dependency checks) |

---

## Requirements (EARS)

### REQ-1: Backend health endpoint
**User Story:** As a frontend (and as an ops probe), I want a health endpoint, so that I can
confirm the backend is alive.

**Acceptance Criteria:**
1. WHEN a client sends `GET /api/health` THE SYSTEM SHALL respond `200` with JSON body `{"status":"ok"}`.
2. WHEN the app is instantiated THE SYSTEM SHALL expose the FastAPI app through an `AsgiFunctionApp`
   adapter in `function_app.py` (the Azure Functions deployment entry point).

**Test Coverage:** `backend/tests/unit/test_health.py::test_health_ok`,
`backend/tests/unit/test_function_app.py::test_asgi_adapter_wraps_app` (+ `::test_adapter_exposes_the_fastapi_app`
asserting the adapter serves the same FastAPI instance).

### REQ-2: Frontend displays backend status
**User Story:** As the operator, I want the dashboard to show whether the backend is healthy,
so that I get immediate visual confirmation the system is up.

**Acceptance Criteria:**
1. WHEN the page loads and `GET /api/health` returns `{"status":"ok"}` THE SYSTEM SHALL display **"All good"**.
2. IF the health request fails, returns a non-ok HTTP status, OR returns a `200` whose body does
   not match `{"status":"ok"}` THEN THE SYSTEM SHALL display a visible error state (e.g.
   **"Backend unavailable"**) rather than a blank screen.
3. WHILE the health request is in flight THE SYSTEM SHALL display a loading indicator.

**Test Coverage:** `frontend/src/App.test.tsx` (Vitest + Testing Library, `fetch` mocked for ok /
error / loading).

### REQ-3: Monorepo structure & packaging
**User Story:** As a developer, I want a correct multi-technology layout, so that both stacks build,
test, and install independently and via one root command.

**Acceptance Criteria:**
1. THE SYSTEM SHALL organize code as `backend/` (Python/uv) and `frontend/` (React/Vite/TS) with a
   top-level `e2e/` and `.github/workflows/`.
2. WHEN a developer runs `make install` THE SYSTEM SHALL install both stacks' dependencies (`uv sync` +
   `npm ci`).
3. WHEN a developer runs `make test` THE SYSTEM SHALL run backend unit tests and frontend unit tests.

**Test Coverage:** verified structurally by CI running green + `make test` succeeding locally.

### REQ-4: End-to-end wiring proven
**User Story:** As a developer, I want one automated test that boots both stacks and confirms they talk,
so that regressions in the wiring are caught.

**Acceptance Criteria:**
1. WHEN the Playwright e2e runs against a live uvicorn backend and Vite frontend THE SYSTEM SHALL load the
   page and assert the visible text **"All good"**.

**Test Coverage:** `e2e/tests/health.spec.ts`.

### REQ-5: CI runs the full test suite
**User Story:** As a maintainer, I want CI to enforce TDD, so that no PR merges with failing tests.

**Acceptance Criteria:**
1. WHEN a pull request is opened THE SYSTEM SHALL run, and require to pass: ruff + pytest (backend),
   ESLint/Prettier + Vitest (frontend), and the Playwright e2e.

**Test Coverage:** `.github/workflows/ci.yml` executing green on the initial PR.

### REQ-6: Coding guidelines committed
**User Story:** As the team, I want conventions written into the repo, so that they persist across sessions.

**Acceptance Criteria:**
1. THE SYSTEM SHALL contain `.specs/steering/structure.md` documenting layout, packaging, TDD workflow,
   naming, lint/format tooling, and per-stack conventions.
2. THE SYSTEM SHALL contain a root `CLAUDE.md` that concisely points to the steering files.

**Test Coverage:** presence + content review (no automated test).

---

## Design

### Directory layout
```
claim-automation-cloud/
├── CLAUDE.md                     # concise pointer to .specs/steering/ (REQ-6)
├── Makefile                      # install / dev / test / lint / e2e (S8)
├── .github/workflows/ci.yml      # REQ-5
├── backend/
│   ├── pyproject.toml            # uv project, py3.12, deps: fastapi, uvicorn,
│   │                             #   azure-functions; dev: pytest, httpx, ruff (S3/S9)
│   ├── uv.lock
│   ├── app/
│   │   ├── __init__.py
│   │   └── main.py               # FastAPI() + GET /api/health  (REQ-1.1)
│   ├── function_app.py           # AsgiFunctionApp(app) — Functions entry (REQ-1.2)
│   ├── pipeline/                 # (empty placeholder pkg for future ported logic)
│   │   └── __init__.py
│   └── tests/
│       ├── conftest.py           # FastAPI TestClient fixture
│       └── unit/
│           ├── test_health.py
│           └── test_function_app.py
├── frontend/
│   ├── package.json              # react, vite, typescript; vitest, @testing-library/*,
│   │                             #   eslint, prettier (S2/S9)
│   ├── vite.config.ts            # dev proxy /api -> localhost:8000 (S10)
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx               # fetch /api/health -> "All good" | error | loading (REQ-2)
│       ├── App.test.tsx
│       └── setupTests.ts
└── e2e/
    ├── package.json              # @playwright/test
    ├── playwright.config.ts      # webServer: uvicorn + vite (S5)
    └── tests/health.spec.ts      # REQ-4
```

### Interfaces
```python
# backend/app/main.py
from fastapi import FastAPI

app = FastAPI(title="claim-automation-cloud")

@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
```
```python
# backend/function_app.py  (Azure Functions v2 ASGI adapter — S1/REQ-1.2)
import azure.functions as func
from app.main import app as fastapi_app

app = func.AsgiFunctionApp(app=fastapi_app,
                           http_auth_level=func.AuthLevel.ANONYMOUS)
```
```tsx
// frontend/src/App.tsx — states: loading | ok ("All good") | error ("Backend unavailable")
// calls fetch("/api/health"); Vite proxy forwards /api -> backend in dev (S10).
```

### Local dev wiring (S10)
- Backend: `uvicorn app.main:app --port 8000` (via `make dev` or run alongside frontend).
- Frontend: `vite` dev server proxies `/api/*` → `http://localhost:8000`, so the SPA uses a
  relative `/api/health` in every environment (dev proxy locally; SWA `/api` route in prod).

### Testing strategy (TDD — RED before GREEN)
| REQ | Test | Level | Tool |
|-----|------|-------|------|
| 1.1 | `test_health_ok` | unit | pytest + FastAPI TestClient |
| 1.2 | `test_asgi_adapter_wraps_app` | unit | pytest (assert `function_app.app` wraps the FastAPI app) |
| 2.1–2.3 | `App.test.tsx` (ok / error / loading) | unit | Vitest + Testing Library (mock `fetch`) |
| 4.1 | `health.spec.ts` | e2e | Playwright (live uvicorn + vite) |

Mocking: frontend unit tests mock `fetch`; backend unit tests hit the real in-process app via
TestClient (no network). The e2e uses **no mocks** — real processes.

### Out of scope
- Any claim-automation logic (parsing, classification, PDF, Trello), auth/OAuth/JWT, Azurite/Table
  Storage, Bicep/infra, and the deploy workflows (`deploy-functions.yml` / `deploy-swa.yml`).
- Readiness/dependency health (Azurite reachability) — liveness only (S11).
- `func start`-based e2e — deferred; uvicorn is the e2e target for now (S5).

---

## Tasks (TDD-first)

| # | Task | Type | REQ |
|---|------|------|-----|
| 0 | Scaffold `structure.md` + root `CLAUDE.md` (conventions) | DOC | 6 |
| 1 | Backend project init: `pyproject.toml` (uv, py3.12), deps, `tests/conftest.py` | SETUP | 3 |
| 2 | Write `test_health.py` + `test_function_app.py` — **RED** | TEST | 1 |
| 3 | Implement `app/main.py` + `function_app.py` — **GREEN** | IMPL | 1 |
| 4 | Frontend project init: Vite+React+TS, ESLint/Prettier, Vitest, `vite.config.ts` proxy | SETUP | 3 |
| 5 | Write `App.test.tsx` (ok/error/loading) — **RED** | TEST | 2 |
| 6 | Implement `App.tsx` + `main.tsx` — **GREEN** | IMPL | 2 |
| 7 | Write `e2e/tests/health.spec.ts` + `playwright.config.ts` (webServer uvicorn+vite) | TEST | 4 |
| 8 | Root `Makefile` (install/dev/test/lint/e2e) | SETUP | 3 |
| 9 | `.github/workflows/ci.yml` — backend, frontend, e2e jobs | IMPL | 5 |

**TDD ordering:** Tasks 2→3 and 5→6 are strict RED→GREEN pairs. Task 7's e2e goes RED until
both 3 and 6 are green. Task 9 CI is the last gate.

### Post-implementation checklist
- [ ] `make test` green (pytest + vitest)
- [ ] `make e2e` green (Playwright shows "All good")
- [ ] `make lint` green (ruff + eslint/prettier)
- [ ] CI green on the initial PR
- [ ] REQ-1..6 each traced to a passing test or committed artifact
- [ ] Constitution: Article I (tests-first) honored — tests committed before impl in each pair
- [ ] No business logic leaked into the skeleton (Out-of-scope respected)
```
