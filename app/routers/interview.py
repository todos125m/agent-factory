import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.blueprint import validate_blueprint
from app.interview import build_blueprint, load_questions, next_question

router = APIRouter(tags=["interview"])


def _state(answers: dict[str, Any]) -> dict[str, Any]:
    questions = load_questions()
    question = next_question(answers, questions)
    if question is not None:
        return {"done": False, "question": question, "answers": answers}
    blueprint = build_blueprint(answers)
    return {"done": True, "question": None, "answers": answers, "blueprint": blueprint, "issues": validate_blueprint(blueprint)}


@router.get("/interview")
def get_next(answers: str = Query(default="{}")) -> dict:
    """`answers`: JSON-encoded `{target: value}` collected so far (client holds the state)."""
    try:
        parsed = json.loads(answers)
    except json.JSONDecodeError as e:
        raise HTTPException(422, "answers must be a JSON object") from e
    return _state(parsed)


@router.post("/interview/answer")
def submit_answer(body: dict) -> dict:
    """body: {"answers": {<target>: <value>, ...}, "target": "<question target>", "value": <answer>}."""
    if "target" not in body:
        raise HTTPException(422, "body must include 'target'")
    answers = dict(body.get("answers") or {})
    answers[body["target"]] = body.get("value")
    return _state(answers)
