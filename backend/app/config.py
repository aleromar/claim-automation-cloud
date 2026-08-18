"""Application settings (pydantic-settings, env-driven).

All fields have test-safe defaults so importing `app.main` never explodes at
collection time. Config comes from process env vars ONLY — exactly like prod
(Azure app settings). Local dev's gitignored .env is injected by the launcher
(`make dev` via `uv run --env-file`), never read here: an app-owned dotenv
source would leak a dev machine's .env into tests (bugfix, 2026-08-11).
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    google_client_id: str = ""
    google_auth_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    google_token_url: str = "https://oauth2.googleapis.com/token"
    oauth_redirect_uri: str = "http://localhost:8000/api/auth/callback"
    operator_email: str = ""  # single-operator gate; startup fails fast when empty
    frontend_base_url: str = "http://localhost:5173"
    secret_store_backend: Literal["file", "keyvault"] = "file"
    secret_store_file_path: str = ".secrets.json"
    key_vault_uri: str | None = None  # required when secret_store_backend == "keyvault"
    table_storage_backend: Literal["connection_string", "managed_identity"] = "connection_string"
    storage_connection_string: str = "UseDevelopmentStorage=true"  # default → local Azurite
    tables_endpoint: str | None = None  # required when table_storage_backend == "managed_identity"
    cors_allowed_origin: str | None = None
    jwt_ttl_hours: int = Field(default=8, gt=0)
    field_extractor_backend: Literal["regex"] = "regex"  # 5a2 registers "llm"
    membretes_container: str = "membretes"  # private Blob container (D26, amended)
    # Consumed by 5c's composition root when it builds the BlobMembreteSource;
    # no validator yet — a hard requirement would break the deployed app before
    # 5c wires the pipeline (blob endpoint needed under managed_identity only).
    blob_endpoint: str | None = None

    @model_validator(mode="after")
    def _keyvault_requires_uri(self) -> "Settings":
        if self.secret_store_backend == "keyvault" and not self.key_vault_uri:
            raise ValueError(
                "KEY_VAULT_URI is not configured — required when SECRET_STORE_BACKEND=keyvault "
                "(set by the infra deployment as a Function App setting)"
            )
        return self

    @model_validator(mode="after")
    def _managed_identity_requires_endpoint(self) -> "Settings":
        if self.table_storage_backend == "managed_identity" and not self.tables_endpoint:
            raise ValueError(
                "TABLES_ENDPOINT is not configured — required when "
                "TABLE_STORAGE_BACKEND=managed_identity "
                "(set by the infra deployment as a Function App setting)"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
