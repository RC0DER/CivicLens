"""Alembic environment for the INTAKE STORE.

    alembic --name intake upgrade head

Separate from the intake history on purpose. A migration able to touch both
stores would be a migration holding credentials for both, which is exactly the
coupling this design refuses.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.config import get_settings  # noqa: E402
from app.db import IntakeBase  # noqa: E402
import app.intake_models  # noqa: F401,E402  (registers the table on IntakeBase)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# An explicitly supplied URL wins, so that CI and one-off maintenance runs can
# target a database without editing the environment. Otherwise the app settings
# are the single source of truth.
url = config.get_main_option("sqlalchemy.url") or get_settings().intake_db_url
if not url:
    raise SystemExit(
        "INTAKE_DB_URL is not set. Intake migrations run from the investigator deployment, "
        "which is the only one holding those credentials."
    )
config.set_main_option("sqlalchemy.url", url)
target_metadata = IntakeBase.metadata


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True,
                      compare_type=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True,
                          render_as_batch=connection.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
