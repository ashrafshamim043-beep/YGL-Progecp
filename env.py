"""
Alembic environment configuration.

Standard async-SQLAlchemy Alembic setup. No migration is executed by this
file alone -- `alembic upgrade head` must be run explicitly against a real
database by an operator, it is never triggered automatically by app startup
or by docker-compose.
"""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

from app.core.config import settings
from app.db.session import Base

# Import all module models so Base.metadata is fully populated for autogenerate.
from app.modules.identity import db_models as identity_models  # noqa: F401
from app.modules.member import db_models as member_models  # noqa: F401
from app.modules.sponsor import db_models as sponsor_models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=settings.database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        {"sqlalchemy.url": settings.database_url}, prefix="sqlalchemy.", poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
