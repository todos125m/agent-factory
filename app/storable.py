"""Text every database can store: PostgreSQL text can't hold a NUL, and no database encodes a lone surrogate
(not UTF-8). Anything that reaches the database only after a paid model call (the owner's chat text, a model's
reply, a provider's error message) is checked or cleaned with this first, so the store can't fail after the spend.

Also what fits a column. SQLite stores any string in a VARCHAR(n) and any integer in an INTEGER; PostgreSQL (production)
refuses both when too long or too big, and a store that fails there is an unmapped 500 that repeats on every retry,
after the spend when the value came from a model. So every value that reaches a column is checked against that
column's own limit first (`limit`), before anything is written: a request body is a 422 naming the field
(`StorableIn`), a model's reply is invalid (`StorableOut`, and the length validators of the classes that hold text
destined for a column). tests/postgres_limits.py makes every test's SQLite refuse what PostgreSQL refuses, so a
column a writer forgot fails the suite.
"""

import math
from collections.abc import Iterator
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.db import varchar_length

# PostgreSQL's INTEGER is 32 bits (SQLite's 64): an API number that lands in an Integer column is bounded by it.
INT32_MAX = 2**31 - 1
Int32 = Annotated[int, Field(ge=-(2**31), le=INT32_MAX)]


def limit(model: type, column: str) -> int:
    """The length of `model`'s String(n) column: the one number every writer of that column checks against, so a
    validator can't drift from the column it guards (any other column has no length: asking for one is a mistake)."""
    length = varchar_length(model.__table__.c[column].type)
    if length is None:
        raise ValueError(f"{model.__name__}.{column} has no length limit")
    return length


def problem(text: str) -> str | None:
    """Why `text` can't be stored, or None if it can."""
    if "\x00" in text:
        return "must not contain NUL characters"
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return "is not valid Unicode (lone surrogate)"
    return None


def clean(text: str) -> str:
    """`text` without what can't be stored (NUL dropped, a lone surrogate becomes '?')."""
    return text.replace("\x00", "").encode("utf-8", "replace").decode("utf-8")


def _scalars(value: Any) -> Iterator[Any]:
    """Every string and number in `value`: a dict's keys and values and a list's items, to any depth. Iterative, since
    a request body can nest as deep as the JSON parser lets it."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
        else:
            yield item


def _strings(value: Any) -> Iterator[str]:
    return (item for item in _scalars(value) if isinstance(item, str))


def _not_finite(value: Any) -> bool:
    """A NaN or Infinity inside a JSON object or list: Python's json writes both as bare tokens (and its parser reads
    them from a request body), which PostgreSQL's json type refuses. A float field of its own is a column of its own
    (double precision takes both) and isn't looked at."""
    return isinstance(value, (dict, list, tuple)) and any(
        isinstance(item, float) and not math.isfinite(item) for item in _scalars(value)
    )


class StorableOut(BaseModel):
    """A model's structured reply. It is stored only after the paid call, so one with a string the database
    can't store is invalid (a 422 with nothing stored), not a 500 at the store."""

    @model_validator(mode="after")
    def _every_string_is_storable(self):
        for text in _strings(self.model_dump()):
            if reason := problem(text):
                raise ValueError(f"reply {reason}")
        return self


class StorableIn(BaseModel):
    """A request body whose values reach the database. A string it can't store (NUL, lone surrogate) or a JSON
    document PostgreSQL refuses (NaN or Infinity inside it) is a 422 naming the field, before anything is written or
    paid for. A String(n) field also declares its length: `Field(max_length=limit(Model, "column"))`."""

    @field_validator("*")
    @classmethod
    def _storable(cls, value: Any) -> Any:
        for text in _strings(value):
            if reason := problem(text):
                raise PydanticCustomError("unstorable_text", "{reason}", {"reason": reason})
        if _not_finite(value):
            raise PydanticCustomError("not_finite", "numbers must be finite (NaN and Infinity can't be stored)")
        return value
