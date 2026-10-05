"""Process-level configuration, read from the environment.

Per-school configuration (tier weights, thresholds, quiet hours, ...) lives on the
tenant row instead — see ``app.tenancy.TenantSettings``.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STEM_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "staging", "production"] = "development"

    # Runtime connection uses a role WITHOUT BYPASSRLS so row-level security applies.
    database_url: str = "postgresql+asyncpg://stem_app:stem_app@localhost:5432/stemtrack"
    # Migrations run as the table owner.
    migration_database_url: str = "postgresql+asyncpg://stem_owner:stem_owner@localhost:5432/stemtrack"
    redis_url: str = "redis://localhost:6379/0"

    # Secrets. Development defaults are deliberately obvious; production refuses them.
    jwt_secret: str = "dev-only-jwt-secret-change-me-0123456789abcdef"  # noqa: S105
    # Fernet key (urlsafe base64, 32 bytes) for field-level encryption of contact details.
    field_encryption_key: str = "q0Zl7mNw1m3o7m2nQh0xvC1x4p9Yv0aJgS3c6q7m2rU="
    # Retired Fernet keys, still accepted for reading until `python -m app.cli rotate-keys` has
    # re-encrypted everything under field_encryption_key (docs/operations.md, "Rotating keys").
    field_encryption_previous_keys: list[str] = Field(default_factory=list)
    # HMAC key for blind indexes (lets us look up an encrypted email without decrypting).
    blind_index_key: str = "dev-only-blind-index-key"

    access_token_minutes: int = 15
    refresh_token_idle_hours: int = 12  # long enough for a full competition day offline
    refresh_token_absolute_days: int = 7
    magic_link_minutes: int = 15
    mfa_required_roles: list[str] = Field(default_factory=lambda: ["programme_admin", "leader"])

    public_base_url: str = "http://localhost:5173"
    api_base_url: str = "http://localhost:8000"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # Object storage. "local" writes under storage_local_path; "s3" uses any S3-compatible endpoint.
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_path: str = "./var/storage"
    s3_endpoint_url: str | None = None
    s3_region: str = "me-central-1"  # UAE region
    s3_bucket: str = "stemtrack"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    signed_url_seconds: int = 300

    # Background jobs: when false, jobs run inline (tests, single-process dev).
    use_job_queue: bool = False

    # Messaging providers. When unset, the "outbox" adapter records sends without delivering.
    email_provider: Literal["outbox", "smtp", "postmark", "ses"] = "outbox"
    email_from: str = "programmes@school.example"
    smtp_host: str | None = None
    smtp_port: int = 587
    postmark_token: str | None = None
    sms_provider: Literal["outbox", "twilio"] = "outbox"
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from: str | None = None
    whatsapp_provider: Literal["outbox", "cloud_api"] = "outbox"
    whatsapp_phone_number_id: str | None = None
    whatsapp_access_token: str | None = None
    whatsapp_verify_token: str = "dev-verify-token"  # noqa: S105
    whatsapp_app_secret: str | None = None

    rate_limit_auth_per_minute: int = 10
    rate_limit_default_per_minute: int = 600

    sentry_dsn: str | None = None
    otel_enabled: bool = False
    log_level: str = "INFO"

    def assert_safe_for_production(self) -> None:
        if self.environment not in ("production", "staging"):
            return
        insecure = [
            name
            for name, value in (
                ("jwt_secret", self.jwt_secret),
                ("blind_index_key", self.blind_index_key),
            )
            if "dev-only" in value
        ]
        if self.field_encryption_key == Settings.model_fields["field_encryption_key"].default:
            insecure.append("field_encryption_key")
        if insecure:
            raise RuntimeError(f"Refusing to start with development secrets: {', '.join(insecure)}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
