"""Alembic migration environment.

Deliberately narrow imports: `app.core.config` (for `settings.database_url`,
the same env-driven connection string `.env.example` already documents) and
`app.db.schema` (for `target_metadata`) — never `app.main`, which is what
actually builds the FastAPI app, installs CORS middleware, and registers
routes. Loading this file never starts the app and never opens a database
connection by itself; `run_migrations_offline` below (the path
`alembic upgrade head --sql` takes) only ever renders SQL text from
`target_metadata`; nothing here is reachable from `run_migrations_online`
unless a caller explicitly runs Alembic without `--sql` against a real
`sqlalchemy.url`.
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# alembic.ini's script_location anchors this file at <backend>/alembic/env.py,
# so <backend> (one level up) is where the `app` package this env imports
# actually lives. Console-script invocation (`alembic upgrade head --sql`)
# does not put the current working directory on sys.path the way `python -m`
# would, so this needs to be explicit rather than relying on how a caller
# happens to invoke Alembic.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.db.schema import metadata  # noqa: E402

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# The real connection string is an application setting (env-driven, see
# .env.example), not a value duplicated into alembic.ini -- one source of
# truth for it, the same as everywhere else this project reads config.
config.set_main_option("sqlalchemy.url", settings.database_url)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# app.db.schema's MetaData -- see that module's own docstring for why this
# is plain SQLAlchemy Core, not a declarative Base.
target_metadata = metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
