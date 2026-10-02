from fastapi import APIRouter

from app.blueprint import validate_blueprint

router = APIRouter(tags=["blueprints"])


@router.post("/blueprints/validate")
def validate(body: dict) -> dict:
    issues = validate_blueprint(body)
    return {"valid": not issues, "issues": issues}
