from alembic import context

from c1a15.config import database_url
from c1a15.db.models import Base
from c1a15.db.session import engine

SCHEMAS = {"ops", "knowledge"}


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name in SCHEMAS
    return True


def run_migrations_online() -> None:
    with engine(context.config.get_main_option("sqlalchemy.url") or database_url()).connect() as connection:
        context.configure(
            connection=connection, target_metadata=Base.metadata, include_schemas=True, include_name=include_name
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
