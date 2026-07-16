.PHONY: install dev seed-dev test lint e2e azurite backend-test frontend-test backend-lint frontend-lint

# Install dependencies for both stacks.
install:
	cd backend && uv sync
	cd frontend && npm ci
	cd e2e && npm ci && npx playwright install chromium

# Local Azurite (Table Storage emulator) — the only containerized piece, so a plain
# `docker run`, no compose (state-store spec, 2026-07-16). Reset: docker rm -f claim-azurite
azurite:
	@docker info >/dev/null 2>&1 || { echo "ERROR: Docker daemon not running — start Docker Desktop (needed for Azurite)"; exit 1; }
	@docker start claim-azurite 2>/dev/null || docker run -d --name claim-azurite \
		-p 10000:10000 -p 10001:10001 -p 10002:10002 \
		mcr.microsoft.com/azure-storage/azurite
	@i=0; until nc -z 127.0.0.1 10002 2>/dev/null; do \
		i=$$((i+1)); if [ $$i -ge 60 ]; then echo "ERROR: Azurite not ready on :10002 after 30s"; exit 1; fi; \
		sleep 0.5; \
	done; echo "Azurite ready on :10002"

# Run backend (uvicorn :8000) and frontend (Vite :5173) together for local dev.
# Ctrl-C stops both.
dev: azurite
	cd backend && uv run uvicorn app.main:app --reload --port 8000 & \
	cd frontend && npm run dev; \
	kill %1 2>/dev/null || true

# One-time local setup: seed the dev secret store with a random session signing
# key (never auto-generated at startup) and a placeholder Google client secret —
# the backend fails fast without both. Replace the placeholder for real logins.
seed-dev:
	cd backend && uv run python -c "import secrets; \
	from app.secret_store import GOOGLE_CLIENT_SECRET, SESSION_SIGNING_KEY, FileSecretStore; \
	s = FileSecretStore('.secrets.json'); \
	s.get(SESSION_SIGNING_KEY) or s.set(SESSION_SIGNING_KEY, secrets.token_urlsafe(48)); \
	s.get(GOOGLE_CLIENT_SECRET) or s.set(GOOGLE_CLIENT_SECRET, 'dev-placeholder-not-a-real-secret'); \
	print('.secrets.json seeded')"

# Unit tests, both stacks.
test: backend-test frontend-test

backend-test: azurite
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
