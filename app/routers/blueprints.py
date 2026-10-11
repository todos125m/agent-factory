from fastapi import APIRouter, Depends

from app.auth import current_user
from app.blueprint import validate_blueprint

# Stateless (nothing stored, no model call), but only for signed-in accounts like every other route.
router = APIRouter(tags=["blueprints"], dependencies=[Depends(current_user)])


@router.post("/blueprints/validate")
def validate(body: dict) -> dict:
    issues = validate_blueprint(body)
    return {"valid": not issues, "issues": issues}
