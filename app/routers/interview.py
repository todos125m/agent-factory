import json
from typing import Any

from fastapi import APIRouter, Query

from app.blueprint import validate_blueprint
from app.errors import AppError
from app.interview import build_blueprint, load_questions, next_question
from app.preferences import DEFAULT_LOCALE, LOCALES

router = APIRouter(tags=["interview"])


def _locale(value: Any) -> str:
    """The question's language; one this version doesn't offer falls back to the default, as the UI does."""
    return value if value in LOCALES else DEFAULT_LOCALE


def _state(answers: Any, locale: str) -> dict[str, Any]:
    if not isinstance(answers, dict):  # a JSON list or number would fail every lookup below as a 500
        raise AppError(422, "invalid_request", "answers must be a JSON object")
    questions = load_questions()
    question = next_question(answers, questions, locale)
    if question is not None:
        return {"done": False, "question": question, "answers": answers}
    blueprint = build_blueprint(answers)
    return {"done": True, "question": None, "answers": answers, "blueprint": blueprint, "issues": validate_blueprint(blueprint)}


@router.get("/interview")
def get_next(answers: str = Query(default="{}"), locale: str = Query(default=DEFAULT_LOCALE)) -> dict:
    """`answers`: JSON-encoded `{target: value}` collected so far (client holds the state). `locale`: fa / en / tr."""
    try:
        parsed = json.loads(answers)
    except json.JSONDecodeError as e:
        raise AppError(422, "invalid_request", "answers must be a JSON object") from e
    return _state(parsed, _locale(locale))


@router.post("/interview/state")
def post_state(body: dict) -> dict:
    """The current question for answers held by the client: {"answers": {...}, "locale": "fa"}. A POST, so free-text
    answers never travel in a URL (request-line limits, access logs) when the UI restores an interview."""
    answers = body.get("answers") or {}
    return _state(answers, _locale(body.get("locale")))


@router.post("/interview/answer")
def submit_answer(body: dict) -> dict:
    """body: {"answers": {<target>: <value>, ...}, "target": "<question target>", "value": <answer>, "locale": "fa"}."""
    if not isinstance(body.get("target"), str):
        raise AppError(422, "invalid_request", "body must include 'target'")
    answers = body.get("answers") or {}
    if not isinstance(answers, dict):
        raise AppError(422, "invalid_request", "answers must be a JSON object")
    answers = {**answers, body["target"]: body.get("value")}
    return _state(answers, _locale(body.get("locale")))
