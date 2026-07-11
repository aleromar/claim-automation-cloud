# Backend — Claim Automation (Cloud)

FastAPI application deployed as an Azure Function via `AsgiFunctionApp`.

```bash
uv sync                         # install (incl. dev group)
uv run uvicorn app.main:app --port 8000   # local dev server
uv run pytest                   # unit tests
uv run ruff check . && uv run ruff format --check .   # lint/format
```

See [.specs/steering/structure.md](../.specs/steering/structure.md) for conventions.
