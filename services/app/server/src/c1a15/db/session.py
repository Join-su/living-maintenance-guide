from sqlalchemy import Engine, create_engine

from c1a15.config import database_url


def engine(url: str | None = None) -> Engine:
    return create_engine(url or database_url(), future=True)
