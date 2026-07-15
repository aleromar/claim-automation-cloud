"""Application settings (pydantic-settings, env-driven).

All fields have test-safe defaults so importing `app.main` never explodes at
collection time; real deployments override via env / gitignored .env.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    google_client_id: str = ""
    google_auth_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    google_token_url: str = "https://oauth2.googleapis.com/token"
    oauth_redirect_uri: str = "http://localhost:8000/api/auth/callback"
    operator_email: str = ""  # single-operator gate; startup fails fast when empty
    frontend_base_url: str = "http://localhost:5173"
    secret_store_backend: Literal["file", "keyvault"] = "file"
    secret_store_file_path: str = ".secrets.json"
    key_vault_uri: str = ""  # required when secret_store_backend == "keyvault"
    cors_allowed_origin: str | None = None
    jwt_ttl_hours: int = Field(default=8, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
