"""Configuration, with production refusing to start when it is unsafe.

The deployment PROFILE is the load-bearing setting in this file.

CivicLens is one codebase deployed as three separate services. What keeps a
departmental officer away from reporter contact details is not a permission
check - it is that the `dept` service is started without the intake database
URL and without the HMAC/encryption keys, so the code paths that could read
contact data cannot be constructed at all. A bug in an authorization check
therefore cannot leak identities; there is nothing in that process to leak.

    PROFILE=public        intake endpoint + public register      (no intake keys)
    PROFILE=dept          departmental portal                    (no intake keys)
    PROFILE=investigator  Ombudsman investigators                (holds intake keys)
    PROFILE=all           single process, local development only

ENV=production turns every "should" below into a startup failure. A service
that boots with a development secret is worse than one that does not boot.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Profile = Literal["public", "dept", "investigator", "all"]
Env = Literal["development", "staging", "production"]

INSECURE_DEFAULTS = {"dev-only-change-me", "change-me", "secret", "changeme", ""}


class ConfigurationError(RuntimeError):
    """Raised at import time. Deliberately fatal."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Env = "development"
    profile: Profile = "all"
    service_name: str = "civiclens-api"
    port: int = 8000
    version: str = "1.1.0"

    # ---------------------------------------------------------------- storage
    case_db_url: str = "sqlite:///./data/case.db"
    intake_db_url: str | None = None

    # What PaaS providers inject. Declared so they reach the validator below;
    # nothing else in the codebase reads them.
    database_url: str | None = None
    intake_database_url: str | None = None
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle_seconds: int = 1800
    db_statement_timeout_ms: int = 15_000

    # ---------------------------------------------------------------- intake keys
    # The bridge from a case number to its contact row.
    intake_hmac_key: str | None = None

    # Sealing is asymmetric, so the service that collects contacts cannot read
    # them: the public service gets the seal (public) half, the investigator
    # service gets the open (private) half.
    intake_seal_key: str | None = None
    intake_open_key: str | None = None

    # ---------------------------------------------------------------- auth
    jwt_secret: str = "dev-only-change-me"  # noqa: S105 - a placeholder production refuses
    jwt_ttl_minutes: int = 20
    upload_token_ttl_minutes: int = 20
    login_max_attempts: int = 3
    lockout_minutes: int = 30
    min_password_length: int = 12

    # ---------------------------------------------------------------- evidence
    storage_backend: Literal["local", "s3"] = "local"
    evidence_dir: str = "./data/evidence"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_region: str = "ap-south-1"
    # Supabase Storage and MinIO need path-style addressing and reject the AWS
    # extensions; AWS S3 accepts both. Defaults suit the compatible services.
    s3_use_path_style: bool = True
    s3_server_side_encryption: str | None = None   # e.g. "AES256" on AWS S3
    signed_url_ttl_seconds: int = 300
    max_evidence_mb: int = 15
    max_evidence_per_case: int = 10
    max_request_bytes: int = 20 * 1024 * 1024

    # ---------------------------------------------------------------- abuse control
    redis_url: str | None = None
    rate_limit_reports_per_office_per_day: int = 120
    rate_limit_login_per_hour: int = 10
    rate_limit_read_per_minute: int = 120
    # Salt for the ephemeral, memory-only client bucket used on read routes.
    # Rotated on every restart so a bucket key cannot be correlated across
    # deployments, and never written to disk.
    client_bucket_salt: str = Field(default_factory=lambda: secrets.token_hex(16))

    # ---------------------------------------------------------------- policy
    assignment_limit_days: int = 14
    contact_retention_days: int = 90
    publish_names_before_finding: bool = False

    # ---------------------------------------------------------------- edge
    cors_origins: str = "*"
    trusted_hosts: str = "*"
    behind_proxy: bool = True
    metrics_enabled: bool = True
    metrics_token: str | None = None

    # Serve the portal from this same service. Convenient for a single-box
    # deployment; put a CDN or nginx in front of it at scale.
    serve_frontend: bool = True
    frontend_dir: str = "../frontend"

    # Hands out live TOTP codes for the seeded accounts so a visitor can see
    # the staff views without enrolling an authenticator. Production refuses
    # to start with this on.
    demo_mode: bool = False
    demo_accounts: str = "MCD/REV/2019/0447,MCD/BPA/2018/0112,OMB/INV/2020/0031"
    demo_password: str = "Register-Counter-Seal-42!"  # noqa: S105 - seed credential

    # ---------------------------------------------------------------- platform conventions
    @model_validator(mode="before")
    @classmethod
    def _adopt_platform_database_urls(cls, data: object) -> object:
        """Accept the variables PaaS providers inject.

        Railway, Render, Heroku and Fly all publish a Postgres connection as
        DATABASE_URL. Mapping it here means a deployment can reference the
        database service directly instead of copying a password into a second
        variable by hand - one fewer place for a credential to be pasted
        wrongly.
        """
        if not isinstance(data, dict):
            return data
        if not data.get("case_db_url") and not data.get("CASE_DB_URL"):
            platform_url = data.get("DATABASE_URL") or data.get("database_url")
            if platform_url:
                data["case_db_url"] = platform_url
        if not data.get("intake_db_url") and not data.get("INTAKE_DB_URL"):
            platform_url = data.get("INTAKE_DATABASE_URL") or data.get("intake_database_url")
            if platform_url:
                data["intake_db_url"] = platform_url
        return data

    @field_validator("case_db_url", "intake_db_url", mode="after")
    @classmethod
    def _normalise_driver(cls, value: str | None) -> str | None:
        """postgres:// and postgresql:// both mean psycopg here.

        Providers hand out the bare scheme; SQLAlchemy needs the driver named,
        and psycopg2 is not installed. Rewriting it is kinder than a stack
        trace about a missing DBAPI on first deploy.
        """
        if not value:
            return value
        if value.startswith("postgres://"):
            return "postgresql+psycopg://" + value[len("postgres://"):]
        if value.startswith("postgresql://"):
            return "postgresql+psycopg://" + value[len("postgresql://"):]
        return value

    @property
    def can_seal_contacts(self) -> bool:
        """Able to accept a follow-up contact and seal it away."""
        return bool(self.intake_db_url and self.intake_hmac_key and self.intake_seal_key)

    @property
    def can_open_contacts(self) -> bool:
        """Able to read a sealed contact back. True only for investigators."""
        return bool(self.intake_db_url and self.intake_hmac_key and self.intake_open_key)

    @property
    def holds_intake_keys(self) -> bool:
        """Kept as the name operators look for in /health/ready, and meaning
        the sensitive capability: can this service read a reporter's contact?"""
        return self.can_open_contacts

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        """Origins allowed to call this API from a browser.

        "none" means exactly that: no cross-origin request is permitted. It is
        the correct setting when the service serves its own portal, which is
        the default deployment - the page and the API share an origin, so CORS
        never comes into it. It is also the strictest possible value, so
        production accepts it where it rejects "*".
        """
        if self.cors_origins.strip().lower() in {"none", "same-origin", ""}:
            return []
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def trusted_host_list(self) -> list[str]:
        return [h.strip() for h in self.trusted_hosts.split(",") if h.strip()]

    # ---------------------------------------------------------------- validation
    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_strength(cls, v: str, info: ValidationInfo) -> str:
        if (info.data.get("env") == "production") and (v in INSECURE_DEFAULTS or len(v) < 32):
            raise ConfigurationError(
                "JWT_SECRET must be at least 32 random characters in production. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        return v

    @model_validator(mode="after")
    def _production_invariants(self) -> Settings:
        if not self.is_production:
            return self

        if self.case_db_url.startswith("sqlite"):
            raise ConfigurationError("SQLite is not supported in production. Point CASE_DB_URL at Postgres.")

        if self.profile == "all":
            raise ConfigurationError(
                "PROFILE=all runs the public, departmental and investigator surfaces in one process, "
                "which defeats the separation. Deploy three services."
            )

        # The guarantee, asserted at boot: only investigators may hold the key
        # that opens a sealed contact.
        if self.profile in ("public", "dept") and self.intake_open_key:
            raise ConfigurationError(
                f"PROFILE={self.profile} was given INTAKE_OPEN_KEY, which decrypts reporter contacts. "
                "Only the investigator service may hold it. This service needs INTAKE_SEAL_KEY "
                "(the public half) to seal contacts, and nothing more."
            )
        if self.profile == "dept" and (self.intake_db_url or self.intake_hmac_key):
            raise ConfigurationError(
                "PROFILE=dept was given intake credentials. The departmental service must not be able to "
                "reach the intake store. Remove INTAKE_DB_URL and INTAKE_HMAC_KEY."
            )
        if self.profile == "investigator" and not self.can_open_contacts:
            raise ConfigurationError(
                "PROFILE=investigator needs INTAKE_DB_URL, INTAKE_HMAC_KEY and INTAKE_OPEN_KEY."
            )
        if self.profile == "public" and not self.can_seal_contacts:
            raise ConfigurationError(
                "PROFILE=public needs INTAKE_DB_URL, INTAKE_HMAC_KEY and INTAKE_SEAL_KEY to seal "
                "follow-up contacts. Give it a write-only database role where the platform allows "
                "one - it has no reason to read the table back, and no key that could."
            )

        if self.storage_backend == "local":
            raise ConfigurationError(
                "Local disk storage is not supported in production. Set STORAGE_BACKEND=s3 so that "
                "evidence downloads are signed, expiring and logged."
            )
        if self.storage_backend == "s3" and not self.s3_bucket:
            raise ConfigurationError("STORAGE_BACKEND=s3 requires S3_BUCKET.")

        if "*" in self.cors_origin_list:
            raise ConfigurationError(
                "CORS_ORIGINS=* is not acceptable in production. Name the portal's origin, or set "
                "CORS_ORIGINS=none when this service serves its own portal and needs no cross-origin access."
            )
        if "*" in self.trusted_host_list:
            raise ConfigurationError("TRUSTED_HOSTS=* is not acceptable in production. Name the API hostname.")
        if self.publish_names_before_finding:
            raise ConfigurationError(
                "PUBLISH_NAMES_BEFORE_FINDING=true publishes unproven allegations against named "
                "individuals. If counsel has cleared this, remove this guard deliberately - it is here "
                "so that nobody enables it by accident."
            )
        if self.demo_mode:
            raise ConfigurationError(
                "DEMO_MODE hands out working one-time codes for seeded accounts. "
                "It must never be on in production."
            )
        if self.metrics_enabled and not self.metrics_token:
            raise ConfigurationError("METRICS_TOKEN is required when metrics are exposed in production.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
