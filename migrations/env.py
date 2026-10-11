"""Alembic environment (docs/FACTORY_IMPLEMENTATION_PLAN.md, owner decision d34).

The app runs the migrations itself at startup (app/db.py::migrate), on its own connection; the `alembic` command line
works too and reads DATABASE_URL like the app. SQLite can't ALTER most of a table, so its migrations run in batch mode
(copy-and-move), which is also what lets a column get a foreign key there.
"""

from alembic import context
from sqlalchemy import engine_from_config, pool

from app import models  # noqa: F401 — registers every table on Base.metadata
from app.config import DATABASE_URL
from app.db import Base

config = context.config
target_metadata = Base.metadata


def _configure(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url") or DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:  # the app's own (app/db.py::migrate)
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
        return
    section = config.get_section(config.config_ini_section) or {}
    section.setdefault("sqlalchemy.url", DATABASE_URL)
    engine = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as conn:
        _configure(conn)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
