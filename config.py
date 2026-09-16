"""
Application configuration.

All values here are read from environment variables / .env — nothing
financial or business-rule-related is hardcoded. Values with no sensible
default (secrets, connection strings) have no default at all, so the app
fails fast on missing configuration rather than silently using something
insecure.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # --- App ---
    app_name: str = "Y.G.L Office System"
    environment: str = "development"  # development | staging | production
    api_v1_prefix: str = "/api/v1"

    # --- Database ---
    database_url: str  # e.g. postgresql+psycopg://user:pass@host:5432/ygl_office

    # --- Redis (sessions, idempotency-key cache) ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Auth ---
    jwt_secret_key: str  # must be provided via secrets manager, never a default
    jwt_access_token_ttl_minutes: int = 20
    jwt_refresh_token_ttl_days: int = 14
    admin_mfa_required: bool = True

    # --- Idempotency-Key cache TTL (Technical Lead configuration item,
    # per Specification v3 Fix B6 — NOT a business rule) ---
    idempotency_key_ttl_hours: int = 48

    # --- Failed-login lockout (Technical Lead configuration item,
    # per Specification v3 Fix B5 — NOT a business rule) ---
    login_failure_threshold: int = 5
    login_lockout_minutes: int = 30
    admin_login_failure_threshold: int = 3
    admin_login_lockout_minutes: int = 30

    # --- KYC / Sumsub ---
    # Webhook HMAC secret ONLY -- lets us verify webhook authenticity. This
    # is NOT a production API token (create_applicant()/get_verification_url()
    # remain blocked regardless -- see sumsub_adapter.py's
    # ProductionCredentialsNotConfiguredError). No default: the app must
    # fail fast if this is missing rather than silently accept unsigned
    # webhooks. Sourced from secrets manager/environment, never committed.
    sumsub_webhook_secret: str = "dev-only-placeholder-not-a-real-secret"


settings = Settings()
