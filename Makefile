.PHONY: install dev env-check seed-dev test lint e2e azurite backend-test frontend-test backend-lint frontend-lint

# Install dependencies for both stacks.
install:
	cd backend && uv sync
	cd frontend && npm ci
	cd e2e && npm ci && npx playwright install chromium

# Local Azurite (Table Storage emulator) — the only containerized piece, so a plain
# `docker run`, no compose (state-store spec, 2026-07-16). Reset: docker rm -f claim-azurite
# All three ports published now: blob/queue (10000/10001) are for `func start` in the worker
# feature, and published ports are fixed at container-create time — which is why a container
# built from a different image is recreated below (worker-controls REQ-7.2: a reused pre-pin
# container would keep the old image and 0.0.0.0 binds forever).
AZURITE_IMAGE := mcr.microsoft.com/azure-storage/azurite:3.36.0

# --skipApiVersionCheck: azure-storage-blob 12.30 speaks a blob API version newer
# than any released Azurite (3.36.0 is current latest); the flag is the vendor's
# documented escape hatch for SDK-ahead-of-emulator. Passing a command overrides
# the image CMD, so the 0.0.0.0 host binds must be restated (pipeline-core, 2026-08-21).
AZURITE_CMD := azurite --skipApiVersionCheck -l /data \
	--blobHost 0.0.0.0 --queueHost 0.0.0.0 --tableHost 0.0.0.0

azurite:
	@docker info >/dev/null 2>&1 || { echo "ERROR: Docker daemon not running — start Docker Desktop (needed for Azurite)"; exit 1; }
	@if [ -n "$$(docker ps -aq -f name='^claim-azurite$$')" ] && \
		{ [ "$$(docker inspect claim-azurite --format '{{.Config.Image}}')" != "$(AZURITE_IMAGE)" ] || \
		  ! docker inspect claim-azurite --format '{{.Config.Cmd}}' | grep -q skipApiVersionCheck; }; then \
		echo "claim-azurite exists with a different image or command — recreating"; \
		docker rm -f claim-azurite >/dev/null; \
	fi
	@docker start claim-azurite 2>/dev/null || docker run -d --name claim-azurite \
		-p 127.0.0.1:10000:10000 -p 127.0.0.1:10001:10001 -p 127.0.0.1:10002:10002 \
		$(AZURITE_IMAGE) $(AZURITE_CMD)
	@i=0; until nc -z 127.0.0.1 10002 2>/dev/null; do \
		i=$$((i+1)); if [ $$i -ge 60 ]; then echo "ERROR: Azurite not ready on :10002 after 30s"; exit 1; fi; \
		sleep 0.5; \
	done; echo "Azurite ready on :10002"

# The app reads process env vars only (prod parity — see core/config.py); the
# launcher injects backend/.env. Checked before azurite so a missing file
# errors before Docker spins up.
env-check:
	@test -f backend/.env || { echo "ERROR: backend/.env missing — cp backend/.env.example backend/.env and fill it in (see backend/README)"; exit 1; }

# Run backend (uvicorn :8000) and frontend (Vite :5173) together for local dev.
# Ctrl-C stops both; the Azurite container keeps running (reset: see azurite above).
# --env-file must precede `uvicorn` (after it, uvicorn silently swallows it).
# Precedence (uv >= 0.5, verified 0.9.21): exported process vars win; .env gap-fills.
dev: env-check azurite
	cd backend && uv run --env-file .env uvicorn app.main:app --reload --port 8000 & \
	cd frontend && npm run dev; \
	kill %1 2>/dev/null || true

# One-time local setup: seed the dev secret store with a random session signing
# key (never auto-generated at startup) and a placeholder Google client secret —
# the backend fails fast without both. Replace the placeholder for real logins.
seed-dev:
	cd backend && uv run python -c "import secrets; \
	from core.secret_store import GOOGLE_CLIENT_SECRET, SESSION_SIGNING_KEY, FileSecretStore; \
	s = FileSecretStore('.secrets.json'); \
	s.get(SESSION_SIGNING_KEY) or s.set(SESSION_SIGNING_KEY, secrets.token_urlsafe(48)); \
	s.get(GOOGLE_CLIENT_SECRET) or s.set(GOOGLE_CLIENT_SECRET, 'dev-placeholder-not-a-real-secret'); \
	print('.secrets.json seeded')"

# Tests: backend unit + integration (vs Azurite), frontend unit.
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

# End-to-end (Playwright boots uvicorn + Vite itself). Azurite first: the dashboard
# fetches table-backed worker status on login (worker-controls REQ-7.3).
e2e: azurite
	cd e2e && npx playwright test
