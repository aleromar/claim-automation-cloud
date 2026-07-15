.PHONY: install dev seed-dev test lint e2e backend-test frontend-test backend-lint frontend-lint

# Install dependencies for both stacks.
install:
	cd backend && uv sync
	cd frontend && npm ci
	cd e2e && npm ci && npx playwright install chromium

# Run backend (uvicorn :8000) and frontend (Vite :5173) together for local dev.
# Ctrl-C stops both.
dev:
	cd backend && uv run uvicorn app.main:app --reload --port 8000 & \
	cd frontend && npm run dev; \
	kill %1 2>/dev/null || true

# One-time local setup: seed the dev secret store with a random session signing
# key (the backend fails fast at startup without one — never auto-generated).
seed-dev:
	cd backend && uv run python -c "import secrets; from app.secret_store import FileSecretStore; \
	s = FileSecretStore('.secrets.json'); \
	s.get('session-signing-key') or s.set('session-signing-key', secrets.token_urlsafe(48)); \
	print('.secrets.json seeded')"

# Unit tests, both stacks.
test: backend-test frontend-test

backend-test:
	cd backend && uv run pytest

frontend-test:
	cd frontend && npm run test

# Lint + format check, both stacks.
lint: backend-lint frontend-lint

backend-lint:
	cd backend && uv run ruff check . && uv run ruff format --check .

frontend-lint:
	cd frontend && npm run lint

# End-to-end (Playwright boots uvicorn + Vite itself).
e2e:
	cd e2e && npx playwright test
