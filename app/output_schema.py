"""An agent's own output schema: the JSON shape its result must have (the owner's "locked structured output").

It is written in the design (`io_contract.output_schema`), carried by the built version and the registered agent, sent to
the model as a response schema, and then checked here with a real JSON Schema validator: a reply that does not follow it
is not a result, whatever the prompt said. The schema is a plain, self-contained one (no `$ref`, no remote anything), small
enough to store and to send."""

import json
from collections.abc import Iterator
from typing import Any

import jsonschema

from app import storable

MAX_SCHEMA_CHARS = 8000
MAX_PROPERTIES = 40
MAX_DEPTH = 5
MAX_REPORTED = 5
# References and ids would let a schema reach outside itself (a file, a URL): never accepted.
FORBIDDEN_KEYS = {"$ref", "$id", "$schema", "$defs", "definitions", "$anchor", "$dynamicRef", "$dynamicAnchor"}


class SchemaError(ValueError):
    """Why a value is not an acceptable output schema (plain text, safe to show)."""


def _walk(node: Any, depth: int = 0) -> Iterator[tuple[str, Any]]:
    if depth > MAX_DEPTH:
        raise SchemaError(f"nested more than {MAX_DEPTH} levels")
    if isinstance(node, dict):
        if len(node.get("properties") or {}) > MAX_PROPERTIES:
            raise SchemaError(f"more than {MAX_PROPERTIES} properties in one object")
        for key, value in node.items():
            yield key, value
            yield from _walk(value, depth + 1)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, depth + 1)


def clean(value: Any) -> dict[str, Any]:
    """The schema as a plain dict, or a SchemaError. `value` may be the JSON text or the object."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as e:
            raise SchemaError(f"not valid JSON ({e.msg})") from e
    if not isinstance(value, dict):
        raise SchemaError("a JSON object is needed")
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(text) > MAX_SCHEMA_CHARS:
        raise SchemaError(f"longer than {MAX_SCHEMA_CHARS} characters")
    for key, item in _walk(value):
        if key in FORBIDDEN_KEYS:
            raise SchemaError(f"'{key}' is not allowed (the schema must stand alone)")
        if isinstance(item, str) and (why := storable.problem(item)):
            raise SchemaError(f"a text in it {why}")
    if value.get("type") != "object" or not isinstance(value.get("properties"), dict) or not value["properties"]:
        raise SchemaError("the top level must be an object with at least one property")
    try:
        jsonschema.Draft202012Validator.check_schema(value)
    except jsonschema.SchemaError as e:
        raise SchemaError(f"not a valid JSON Schema: {e.message[:200]}") from e
    return json.loads(text)


def errors(schema: dict[str, Any], value: Any) -> list[str]:
    """Why `value` does not follow `schema` (at most a few lines, each 'where: what'); empty when it does."""
    validator = jsonschema.Draft202012Validator(schema)
    found = sorted(validator.iter_errors(value), key=lambda e: [str(p) for p in e.absolute_path])
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message[:200]}" for e in found[:MAX_REPORTED]]


def as_text(schema: dict[str, Any]) -> str:
    return json.dumps(schema, ensure_ascii=False, sort_keys=True)
