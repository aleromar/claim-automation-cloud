"""settings-env-launcher bugfix: Settings must never read a .env file itself.

Prod parity: config comes from process env vars only (Azure app settings).
Local dev's gitignored .env is injected by the launcher (make dev via
`uv run --env-file`), never by the app — otherwise a dev machine's .env
leaks into every test that constructs Settings (the OPERATOR_EMAIL
monkeypatch.delenv incident, 2026-08-11).
"""

from app.config import Settings


def test_settings_ignore_dotenv_in_cwd(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("OPERATOR_EMAIL=leak@example.com\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPERATOR_EMAIL", raising=False)
    assert Settings().operator_email == ""
