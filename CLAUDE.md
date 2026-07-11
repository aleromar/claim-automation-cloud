# CLAUDE.md — Claim Automation (Cloud)

Monorepo: **Python backend** (FastAPI on Azure Functions) + **JavaScript/TypeScript frontend**
(React + Vite → Azure Static Web Apps). A cloud re-implementation of the `../claim_automation`
laptop app. This is a **spec-driven-dev** project.

## Read the steering files first

Authoritative project context lives in `.specs/steering/` — read before any work:

- [constitution.md](.specs/steering/constitution.md) — governance rules (MUST/MUST NOT). Article I: **tests before code**.
- [product.md](.specs/steering/product.md) — what this is, actors, capabilities, scope.
- [tech.md](.specs/steering/tech.md) — architecture, building blocks, decision log (D1–D26).
- [structure.md](.specs/steering/structure.md) — **repo layout, toolchain, testing & coding conventions**.
- [azure-implementation.md](.specs/azure-implementation.md) — authoritative Azure service mapping.

Per-feature specs live in `.specs/{feature}/` (e.g. `.specs/walking-skeleton/spec.md`).

## Conventions in one breath

- **TDD-first**: RED → GREEN → REFACTOR. No implementation without a failing test.
- **Backend**: uv, Python 3.12, ruff; tests in `backend/tests/` (`test_*.py`).
- **Frontend**: npm, TypeScript, Vite, ESLint/Prettier; tests **co-located** (`*.test.tsx`), Vitest.
- **E2E**: Playwright, real uvicorn + Vite, one cross-stack smoke test.
- **Task runner**: `make install | dev | test | lint | e2e`.
- **API paths**: `/api/*` (relative; Vite proxy in dev, SWA route in prod).
- **Simplicity first**; no Docker until a feature needs local Azurite (see structure.md).
