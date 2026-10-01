"""Text every database can store: PostgreSQL text can't hold a NUL, and no database encodes a lone surrogate
(not UTF-8). Anything that reaches the database only after a paid model call (the owner's chat text, a model's
reply, a provider's error message) is checked or cleaned with this first, so the store can't fail after the spend.
"""

from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel, model_validator


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


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


class StorableOut(BaseModel):
    """A model's structured reply. It is stored only after the paid call, so one with a string the database
    can't store is invalid (a 422 with nothing stored), not a 500 at the store."""

    @model_validator(mode="after")
    def _every_string_is_storable(self):
        for text in _strings(self.model_dump()):
            if reason := problem(text):
                raise ValueError(f"reply {reason}")
        return self
