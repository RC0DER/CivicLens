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
    "intake_enc_key": None,
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


def test_public_service_may_not_hold_the_decryption_key():
    """Public seals contacts; only the investigator service opens them."""
    with pytest.raises(ConfigurationError, match="Only the investigator service"):
        _settings(profile="public", intake_db_url="postgresql+psycopg://u@intake-db/intake",
                  intake_hmac_key="k", intake_enc_key="enc")


def test_single_process_deployment_is_refused_in_production():
    with pytest.raises(ConfigurationError, match="defeats the separation"):
        _settings(profile="all", intake_db_url="postgresql+psycopg://u@intake-db/intake",
                  intake_hmac_key="k", intake_enc_key="e")


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
    assert s.holds_intake_keys is False
    assert s.is_production is True


def test_a_correct_investigator_deployment_starts():
    s = _settings(profile="investigator", intake_db_url="postgresql+psycopg://u@intake-db/intake",
                  intake_hmac_key="k", intake_enc_key="e")
    assert s.holds_intake_keys is True


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
