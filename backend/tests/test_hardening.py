"""Production guards: the checks that decide whether this is safe to deploy."""

from __future__ import annotations

import logging

import pytest

from app.config import ConfigurationError, Settings
from app.observability import JsonFormatter, scrub

# Explicit values beat both the .env file and the environment, so each case
# below describes exactly one deployment rather than inheriting the suite's.
PROD = {
    "env": "production",
    "intake_db_url": None,
    "intake_hmac_key": None,
    "intake_seal_key": None,
    "intake_open_key": None,
    "jwt_secret": "x" * 48,
    "case_db_url": "postgresql+psycopg://u@case-db/cases",
    "storage_backend": "s3",
    "s3_bucket": "civiclens-evidence",
    "cors_origins": "https://civiclens.gov.example",
    "trusted_hosts": "api.civiclens.gov.example",
    "metrics_token": "scrape-token",
}


def _settings(**overrides) -> Settings:
    # _env_file=None so these assertions describe the code, not whatever the
    # developer happens to have in .env.
    return Settings(_env_file=None, **{**PROD, **overrides})


# --------------------------------------------------------------------------- refuses to boot
def test_dept_service_given_intake_credentials_refuses_to_start():
    """The guarantee asserted at boot, not just in code review."""
    with pytest.raises(ConfigurationError, match="must not be able to reach the intake store"):
        _settings(profile="dept", intake_db_url="postgresql+psycopg://u@intake-db/intake",
                  intake_hmac_key="k")


def test_public_service_may_not_hold_the_opening_key():
    """Public seals contacts; only the investigator service opens them."""
    with pytest.raises(ConfigurationError, match="Only the investigator service"):
        _settings(profile="public", intake_db_url="postgresql+psycopg://u@intake-db/intake",
                  intake_hmac_key="k", intake_seal_key="seal", intake_open_key="open")


def test_single_process_deployment_is_refused_in_production():
    with pytest.raises(ConfigurationError, match="defeats the separation"):
        _settings(profile="all", intake_db_url="postgresql+psycopg://u@intake-db/intake",
                  intake_hmac_key="k", intake_seal_key="s", intake_open_key="o")


def test_development_secret_is_refused_in_production():
    with pytest.raises(ConfigurationError, match="JWT_SECRET"):
        _settings(profile="dept", jwt_secret="dev-only-change-me")


def test_sqlite_is_refused_in_production():
    with pytest.raises(ConfigurationError, match="SQLite is not supported"):
        _settings(profile="dept", case_db_url="sqlite:///./data/case.db")


def test_local_evidence_storage_is_refused_in_production():
    with pytest.raises(ConfigurationError, match="Local disk storage"):
        _settings(profile="dept", storage_backend="local")


def test_wildcard_cors_is_refused_in_production():
    with pytest.raises(ConfigurationError, match="CORS_ORIGINS"):
        _settings(profile="dept", cors_origins="*")


def test_cors_none_is_accepted_and_allows_no_origin():
    """A service that serves its own portal needs no cross-origin access at
    all - and saying so must not require inventing a hostname before the
    platform has assigned one."""
    for value in ("none", "same-origin", ""):
        s = _settings(profile="dept", cors_origins=value)
        assert s.cors_origin_list == []


def test_publishing_unproven_names_requires_a_deliberate_code_change():
    with pytest.raises(ConfigurationError, match="publishes unproven allegations"):
        _settings(profile="dept", publish_names_before_finding=True)


def test_a_correct_dept_deployment_starts():
    s = _settings(profile="dept")
    assert s.can_open_contacts is False
    assert s.can_seal_contacts is False
    assert s.is_production is True


def test_a_correct_investigator_deployment_starts():
    s = _settings(profile="investigator", intake_db_url="postgresql+psycopg://u@intake-db/intake",
                  intake_hmac_key="k", intake_seal_key="s", intake_open_key="o")
    assert s.can_open_contacts is True


def test_a_correct_public_deployment_seals_but_cannot_open():
    """The collecting service holds only the public half."""
    s = _settings(profile="public", intake_db_url="postgresql+psycopg://u@intake-db/intake",
                  intake_hmac_key="k", intake_seal_key="s")
    assert s.can_seal_contacts is True
    assert s.can_open_contacts is False
    assert s.holds_intake_keys is False


def test_sealing_is_one_way_for_the_service_that_collects():
    """The heart of it: the internet-facing service cannot read back what it
    collected, even holding its own configuration and the ciphertext."""
    from app import security

    seal_key, open_key = security.generate_intake_keypair()
    original = security.get_settings

    public = Settings(_env_file=None, intake_seal_key=seal_key, intake_open_key=None)
    security.get_settings = lambda: public  # type: ignore[assignment]
    try:
        sealed = security.seal_contact("9810012345")
        assert b"9810012345" not in sealed
        with pytest.raises(security.IntakeKeysUnavailable):
            security.open_contact(sealed)

        investigator = Settings(_env_file=None, intake_seal_key=seal_key, intake_open_key=open_key)
        security.get_settings = lambda: investigator  # type: ignore[assignment]
        assert security.open_contact(sealed) == "9810012345"

        # A different recipient key cannot open it either.
        _, other_open = security.generate_intake_keypair()
        wrong = Settings(_env_file=None, intake_seal_key=seal_key, intake_open_key=other_open)
        security.get_settings = lambda: wrong  # type: ignore[assignment]
        assert security.open_contact(sealed) is None
    finally:
        security.get_settings = original  # type: ignore[assignment]


def test_each_sealing_is_unique():
    """Fresh ephemeral key per message: identical contacts do not produce
    identical ciphertext, so the store cannot be scanned for repeats."""
    from app import security

    seal_key, _ = security.generate_intake_keypair()
    original = security.get_settings
    security.get_settings = lambda: Settings(_env_file=None, intake_seal_key=seal_key)  # type: ignore[assignment]
    try:
        assert security.seal_contact("9810012345") != security.seal_contact("9810012345")
    finally:
        security.get_settings = original  # type: ignore[assignment]


# --------------------------------------------------------------------------- logs
def test_log_formatter_redacts_identifiers():
    """A developer who interpolates a phone number into a log line does not
    thereby create a record linking a person to a filing."""
    record = logging.LogRecord("civiclens.test", logging.INFO, __file__, 1,
                               "contacted 9810012345 from 203.0.113.9 re raju@example.com",
                               None, None)
    line = JsonFormatter().format(record)
    assert "9810012345" not in line
    assert "203.0.113.9" not in line
    assert "raju@example.com" not in line
    assert "[redacted-phone]" in line


def test_sensitive_keys_are_masked_in_structured_context():
    masked = scrub({"case_no": "CRTP-2026-004417", "followup_contact": "9810012345",
                    "nested": {"authorization": "Bearer abc"}})
    assert masked["case_no"] == "CRTP-2026-004417"  # not sensitive on a staff action
    assert masked["followup_contact"] == "[redacted]"
    assert masked["nested"]["authorization"] == "[redacted]"


# --------------------------------------------------------------------------- rate limiting
def test_rate_limiter_refuses_a_burst_then_recovers():
    from app import ratelimit

    ratelimit.reset_for_tests()
    bucket = "test:burst"
    assert all(ratelimit.check(bucket, 3, 60).allowed for _ in range(3))
    verdict = ratelimit.check(bucket, 3, 60)
    assert verdict.allowed is False
    assert verdict.retry_after > 0


def test_client_bucket_does_not_contain_the_address():
    from app import ratelimit

    bucket = ratelimit.client_bucket("203.0.113.9")
    assert "203.0.113.9" not in bucket
    assert bucket != ratelimit.client_bucket("203.0.113.10")


# --------------------------------------------------------------------------- passwords
@pytest.mark.parametrize("weak", ["short1!A", "alllowercase123", "12345678901234", "demo-password"])
def test_weak_departmental_passwords_are_refused(weak):
    from app.security import WeakPassword, check_password_policy

    with pytest.raises(WeakPassword):
        check_password_policy(weak)


def test_a_reasonable_passphrase_is_accepted():
    from app.security import check_password_policy

    check_password_policy("Correct-Horse-Battery-7")


# --------------------------------------------------------------------------- storage
def test_local_signed_url_expires_and_resists_tampering():
    from app.storage import LocalStorage, verify_local

    store = LocalStorage("./data/evidence")
    url = store.signed_url("abc123.jpg", ttl_seconds=60)
    expires = int(url.split("expires=")[1].split("&")[0])
    signature = url.split("signature=")[1]

    assert verify_local("abc123.jpg", expires, signature) is True
    assert verify_local("abc123.jpg", expires, "0" * 32) is False       # forged
    assert verify_local("other.jpg", expires, signature) is False       # swapped object
    assert verify_local("abc123.jpg", expires - 3600, signature) is False  # stale


# --------------------------------------------------------------------------- platform deploys
@pytest.fixture
def clean_platform_env(monkeypatch):
    """A PaaS container has no CASE_DB_URL - only DATABASE_URL. The suite sets
    both, so they are removed here to describe the real deployment."""
    monkeypatch.delenv("CASE_DB_URL", raising=False)
    monkeypatch.delenv("INTAKE_DB_URL", raising=False)
    return monkeypatch


def test_platform_database_url_is_adopted_and_given_a_driver(clean_platform_env):
    """Railway, Render, Heroku and Fly all inject DATABASE_URL with a bare
    scheme. SQLAlchemy needs the driver named, and psycopg2 is not installed -
    so an un-rewritten URL fails on first boot with a confusing error."""
    s = Settings(_env_file=None, database_url="postgresql://u:p@host:5432/railway")
    assert s.case_db_url == "postgresql+psycopg://u:p@host:5432/railway"

    heroku_style = Settings(_env_file=None, database_url="postgres://u:p@host:5432/db")
    assert heroku_style.case_db_url == "postgresql+psycopg://u:p@host:5432/db"


def test_an_explicit_url_beats_the_platform_variable(clean_platform_env):
    s = Settings(_env_file=None, database_url="postgresql://u:p@host/db",
                 case_db_url="sqlite:///./data/case.db")
    assert s.case_db_url == "sqlite:///./data/case.db"


def test_intake_platform_url_is_separate_from_the_case_one(clean_platform_env):
    s = Settings(_env_file=None,
                 database_url="postgresql://u:p@case-host/cases",
                 intake_database_url="postgresql://i:q@intake-host/intake")
    assert "case-host" in s.case_db_url
    assert s.intake_db_url is not None and "intake-host" in s.intake_db_url
    assert s.case_db_url != s.intake_db_url


def test_wildcard_hostnames_are_usable_for_platform_subdomains():
    s = Settings(_env_file=None, trusted_hosts="*.up.railway.app, healthcheck.railway.app")
    assert s.trusted_host_list == ["*.up.railway.app", "healthcheck.railway.app"]


# --------------------------------------------------------------------------- object storage
def test_s3_client_suits_compatible_services(monkeypatch):
    """"S3-compatible" is not uniform.

    Supabase Storage and MinIO need path-style addressing and reject the AWS
    extensions. Sending an ACL to them fails every upload - which presents as a
    500 on an otherwise healthy deployment.
    """
    import app.storage as storage_module

    captured: dict = {}

    class FakeClient:
        def put_object(self, **kwargs):
            captured["put"] = kwargs

    def fake_boto_client(service, **kwargs):
        captured["client"] = {"service": service, **kwargs}
        return FakeClient()

    import boto3

    monkeypatch.setattr(boto3, "client", fake_boto_client)
    monkeypatch.setattr(storage_module, "get_settings", lambda: Settings(
        _env_file=None, storage_backend="s3", s3_bucket="civiclens-evidence",
        s3_endpoint_url="https://ref.supabase.co/storage/v1/s3", s3_region="ap-northeast-1",
    ))

    store = storage_module.S3Storage()
    store.put("abc123.jpg", b"\xff\xd8\xff", "image/jpeg")

    assert captured["client"]["endpoint_url"] == "https://ref.supabase.co/storage/v1/s3"
    assert captured["client"]["config"].s3["addressing_style"] == "path"
    assert captured["client"]["config"].signature_version == "s3v4"

    # The two options that break compatible services must not be sent.
    assert "ACL" not in captured["put"]
    assert "ServerSideEncryption" not in captured["put"]
    assert captured["put"]["ContentType"] == "image/jpeg"


def test_server_side_encryption_is_sent_when_configured(monkeypatch):
    """On AWS S3 proper, encryption at rest is still available - opt in."""
    import app.storage as storage_module

    captured: dict = {}

    class FakeClient:
        def put_object(self, **kwargs):
            captured.update(kwargs)

    import boto3

    monkeypatch.setattr(boto3, "client", lambda service, **kw: FakeClient())
    monkeypatch.setattr(storage_module, "get_settings", lambda: Settings(
        _env_file=None, storage_backend="s3", s3_bucket="b", s3_server_side_encryption="AES256",
    ))
    storage_module.S3Storage().put("k.jpg", b"x", "image/jpeg")
    assert captured["ServerSideEncryption"] == "AES256"


def test_storage_failures_are_reported_not_swallowed(monkeypatch):
    import app.storage as storage_module

    class ExplodingClient:
        def put_object(self, **kwargs):
            raise RuntimeError("AccessDenied")

    import boto3

    monkeypatch.setattr(boto3, "client", lambda service, **kw: ExplodingClient())
    monkeypatch.setattr(storage_module, "get_settings", lambda: Settings(
        _env_file=None, storage_backend="s3", s3_bucket="b",
    ))
    with pytest.raises(storage_module.StorageError, match="could not store"):
        storage_module.S3Storage().put("k.jpg", b"x", "image/jpeg")
