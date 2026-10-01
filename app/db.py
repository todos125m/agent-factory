from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import DATABASE_URL


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


engine = make_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def ensure_indexes(bind) -> None:
    """`create_all` only creates the indexes of the tables it creates, so add what was declared since to the
    tables that already exist (idempotent; until the schema moves to migrations)."""
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            index.create(bind, checkfirst=True)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
