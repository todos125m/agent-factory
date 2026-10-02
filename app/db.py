import logging
from collections.abc import Iterator

from sqlalchemy import Column, Enum, String, Table, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import DATABASE_URL

log = logging.getLogger(__name__)

# How long a widening waits for its table's lock before giving up: fail with PostgreSQL's own message rather than
# hang the startup behind a transaction that never ends (the ALTER queues every other query on the table behind it).
LOCK_TIMEOUT = "15s"


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


def varchar_length(column_type) -> int | None:
    """n of a VARCHAR(n) column type; None for any other (Text, a native Enum, a number, JSON)."""
    return column_type.length if isinstance(column_type, String) and not isinstance(column_type, Enum) else None


def narrower_columns(bind) -> list[tuple[Table, Column, int]]:
    """The String(n) columns of existing tables that are shorter in the database than in the models, with the length
    the model declares: a column was widened after its table was created (`create_all` never alters a table)."""
    inspector = inspect(bind)
    found = []
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        live = {c["name"]: varchar_length(c["type"]) for c in inspector.get_columns(table.name)}
        for column in table.columns:
            declared = varchar_length(column.type)
            if declared and live.get(column.name) and live[column.name] < declared:
                found.append((table, column, declared))
    return found


def widen_statement(dialect, table: Table, column: Column, length: int) -> str:
    quote = dialect.identifier_preparer.quote
    return f"ALTER TABLE {quote(table.name)} ALTER COLUMN {quote(column.name)} TYPE VARCHAR({length})"


def ensure_column_lengths(bind) -> None:
    """Widen, in the tables that already exist, every String(n) column the models declare longer than it is
    (idempotent, like `ensure_indexes`; until the schema moves to migrations). PostgreSQL only: SQLite doesn't enforce
    a length (and can't alter a column's type), so there is nothing to refuse there. It only ever widens, which
    PostgreSQL does without rewriting the table or losing a value; a column that is longer than the model says is left
    alone."""
    if bind.dialect.name != "postgresql":
        return
    for table, column, length in narrower_columns(bind):
        with bind.begin() as connection:
            connection.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
            connection.execute(text(widen_statement(bind.dialect, table, column, length)))
        log.warning("widened %s.%s to VARCHAR(%d): the models declare it longer than this database had it",
                    table.name, column.name, length)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
