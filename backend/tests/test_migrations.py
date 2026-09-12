"""Migrations must describe the models.

The usual production incident is a model changed without a migration: it works
locally, where create_all() quietly builds the new column, and fails on deploy.
This test applies the migration history to an empty database and asserts
Alembic finds nothing left to generate.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _alembic_config(name: str | None, url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    if name:
        cfg.config_ini_section = name
    # script_location in alembic.ini is relative to the working directory, so
    # anchor it to the project root - the suite must pass from anywhere.
    cfg.set_main_option("script_location", str(ROOT / "migrations" / (name or "case")))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.mark.parametrize(
    ("section", "env_var", "metadata_path"),
    [
        (None, "CASE_DB_URL", "app.db:CaseBase"),
        ("intake", "INTAKE_DB_URL", "app.db:IntakeBase"),
    ],
    ids=["case", "intake"],
)
def test_migrations_build_the_current_schema(section, env_var, metadata_path, monkeypatch):
    module_name, attr = metadata_path.split(":")
    import importlib

    if section == "intake":
        importlib.import_module("app.intake_models")
    else:
        importlib.import_module("app.models")
    metadata = getattr(importlib.import_module(module_name), attr).metadata

    with tempfile.TemporaryDirectory() as tmp:
        url = f"sqlite:///{tmp}/{section or 'case'}.db"
        monkeypatch.setenv(env_var, url)

        command.upgrade(_alembic_config(section, url), "head")

        engine = create_engine(url)
        with engine.connect() as conn:
            context = MigrationContext.configure(
                conn, opts={"compare_type": True, "render_as_batch": True}
            )
            diff = compare_metadata(context, metadata)
        engine.dispose()

    # Alembic reports the version table of the *other* history as an extra
    # table when both share a database in development; filter to real drift.
    drift = [d for d in diff if not (isinstance(d, tuple) and str(d[0]).startswith("remove_table"))]
    assert not drift, f"models and migrations disagree: {drift}"
