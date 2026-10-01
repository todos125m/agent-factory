"""PostgreSQL's value limits, enforced on SQLite for the whole suite.

SQLite stores anything in a VARCHAR(n) or an INTEGER column; PostgreSQL (production, docker-compose) refuses what does
not fit, so a value that passes every test here fails at the store there: as an unmapped 500, after the paid model call
that produced it, again on every retry. `install()` (called by tests/conftest.py) checks every value written by an
INSERT or UPDATE the way PostgreSQL does and raises the DataError PostgreSQL would, so any test that stores such a value
fails here, not in production. It applies to every engine in the process, including the ones tests build themselves.

What PostgreSQL refuses is `VERDICTS`, measured on PostgreSQL 16.14 with psycopg 3.3 (tests/test_postgres_parity.py
re-checks every verdict against a real server whenever AGENT_FACTORY_TEST_PG_URL is set, so the emulation can't drift):
- a string longer than a VARCHAR(n) column's n characters (characters, not bytes: an emoji counts 1), except that excess
  trailing spaces (only spaces, not tabs or NBSP) are cut off silently;
- a NUL character in any text column;
- an INTEGER outside 32 bits (SMALLINT 16, BIGINT 64);
- a JSON document containing NaN or Infinity (Python writes both as bare tokens; its own parser accepts them).
Not enforced here because SQLite refuses it too: a lone surrogate (not UTF-8, so no database can encode it).
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from sqlalchemy import JSON, BigInteger, Enum, Integer, SmallInteger, String, Text, event, exc
from sqlalchemy.engine import Engine

_INSTALLED = False
_UNENFORCED: ContextVar[bool] = ContextVar("postgres_limits_unenforced", default=False)


@contextmanager
def unenforced() -> Iterator[None]:
    """For the rare test that must store what PostgreSQL would refuse: a row only a SQLite database can hold, from
    before the value was validated. Say why at the call site; everything else is enforced."""
    token = _UNENFORCED.set(True)
    try:
        yield
    finally:
        _UNENFORCED.reset(token)


def _no_constant(name: str) -> None:
    raise ValueError(name)


def refusal(column_type: Any, value: Any) -> str | None:
    """Why PostgreSQL would refuse `value` for a column of `column_type` (the server's own message), or None.
    (Written out here, not shared with app/: this module is the independent statement of what the server does.)"""
    if isinstance(value, str) and isinstance(column_type, String) and not isinstance(column_type, Enum):
        # An Enum is a String to SQLAlchemy but a native enum type on PostgreSQL: it has no length.
        if "\x00" in value:
            return "PostgreSQL text fields cannot contain NUL (0x00) bytes"
        if column_type.length is not None and len(value.rstrip(" ")) > column_type.length:
            return f"value too long for type character varying({column_type.length})"
    elif isinstance(value, int) and not isinstance(value, bool) and isinstance(column_type, Integer):
        bits, name = (16, "smallint") if isinstance(column_type, SmallInteger) else (
            (64, "bigint") if isinstance(column_type, BigInteger) else (32, "integer"))
        if not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
            return f"{name} out of range"
    elif isinstance(value, str) and isinstance(column_type, JSON):
        try:  # the processed value of a JSON column is its serialized text
            json.loads(value, parse_constant=_no_constant)
        except ValueError:
            return "invalid input syntax for type json"
    return None


# (label, column type, value as the application hands it over, the message PostgreSQL answers with, or None: it stores it)
_TOO_LONG = "value too long for type character varying(5)"
_NUL = "PostgreSQL text fields cannot contain NUL (0x00) bytes"
_PERSIAN = "سلاما"  # five letters
_NAN_JSON = {"x": float("nan")}
VERDICTS: list[tuple[str, Any, Any, str | None]] = [
    ("varchar(5): exactly 5 characters", String(5), "abcde", None),
    ("varchar(5): 6 characters", String(5), "abcdef", _TOO_LONG),
    ("varchar(5): 5 emoji (characters, not bytes)", String(5), "\U0001F642" * 5, None),
    ("varchar(5): 6 emoji", String(5), "\U0001F642" * 6, _TOO_LONG),
    ("varchar(5): 5 Persian letters", String(5), _PERSIAN, None),
    ("varchar(5): 6 Persian letters", String(5), _PERSIAN + "ن", _TOO_LONG),
    ("varchar(5): a letter and its combining accent are 2 characters", String(5), "é" * 3, _TOO_LONG),
    ("varchar(5): excess trailing spaces are cut off", String(5), "abcde   ", None),
    ("varchar(5): only spaces", String(5), " " * 10, None),
    ("varchar(5): a space after a 6th character", String(5), "abcdef ", _TOO_LONG),
    ("varchar(5): a tab is not a space", String(5), "abcde\t", _TOO_LONG),
    ("varchar(5): a no-break space is not a space", String(5), "abcde ", _TOO_LONG),
    ("varchar(5): a newline is not a space", String(5), "abcde\n", _TOO_LONG),
    ("varchar(5): NUL", String(5), "a\x00b", _NUL),
    ("text: NUL", Text(), "a\x00b", _NUL),
    ("text: no length limit", Text(), "x" * 100_000, None),
    ("integer: the largest", Integer(), 2**31 - 1, None),
    ("integer: one more", Integer(), 2**31, "integer out of range"),
    ("integer: the smallest", Integer(), -(2**31), None),
    ("integer: one less", Integer(), -(2**31) - 1, "integer out of range"),
    ("smallint: the largest", SmallInteger(), 2**15 - 1, None),
    ("smallint: one more", SmallInteger(), 2**15, "smallint out of range"),
    ("bigint: the largest", BigInteger(), 2**63 - 1, None),
    ("bigint: one more", BigInteger(), 2**63, "bigint out of range"),
    ("json: NaN", JSON(), _NAN_JSON, "invalid input syntax for type json"),
    ("json: Infinity", JSON(), {"x": [float("inf")]}, "invalid input syntax for type json"),
    ("json: the text 'NaN' is only text", JSON(), {"x": "NaN"}, None),
    ("json: NUL inside a string (escaped)", JSON(), {"x": "a\x00b"}, None),
    ("json: a lone surrogate inside a string (escaped)", JSON(), {"x": "a\ud83db"}, None),
]


def _written_values(compiled: Any, parameters: Any) -> Iterator[tuple[Any, Any]]:
    """(column type, value) of every value an INSERT or UPDATE writes: one statement or an executemany. A bind of an
    UPDATE's WHERE clause is a comparison, which PostgreSQL never refuses: only a bind named after a column of the
    table (its SET clause) is a value written."""
    columns = compiled.statement.table.c if compiled.isupdate else None
    for params in parameters if isinstance(parameters, list) else [parameters]:
        named = params.items() if isinstance(params, dict) else zip(compiled.positiontup, params)
        for name, value in named:
            bind = compiled.binds.get(name)
            if bind is not None and (columns is None or name in columns):
                yield bind.type, value


def _check_write(conn, cursor, statement, parameters, context, executemany) -> None:
    compiled = getattr(context, "compiled", None)  # None for a plain string; a DDL compiler has no isinsert
    if conn.dialect.name != "sqlite" or not (getattr(compiled, "isinsert", False) or getattr(compiled, "isupdate", False)):
        return
    if _UNENFORCED.get():
        return
    for column_type, value in _written_values(compiled, parameters):
        if (why := refusal(column_type, value)) is not None:
            raise exc.DataError(statement, parameters, sqlite3.DataError(why))


def install() -> None:
    """Idempotent. On the Engine class, so engines created later (by fixtures or by a test) are covered too."""
    global _INSTALLED
    if not _INSTALLED:
        event.listen(Engine, "before_cursor_execute", _check_write)
        _INSTALLED = True
