"""API errors the UI can show in the owner's language (docs/HUMAN_GUIDED_FACTORY.md §4).

Every error body keeps `detail` (English, for logs, developers and existing clients) and adds a stable `code` with its
`params`; the UI turns the code into its own text (web/i18n.js `err.<code>`) and shows `detail` only to a developer
or when it knows no text for the code. A code is a contract: rename one and the UI falls back to `detail`.
"""

from typing import Any

from fastapi import HTTPException

from app.state_machine import ProjectPaused


class AppError(HTTPException):
    def __init__(self, status_code: int, code: str, detail: str, **params: Any) -> None:
        super().__init__(status_code, detail)
        self.code = code
        self.params = params


def not_found(resource: str, detail: str | None = None, **params: Any) -> AppError:
    """404 for a missing `resource` (project, task, user, ...)."""
    return AppError(404, "not_found", detail or f"{resource.capitalize()} not found", resource=resource, **params)


def refused(exc: Exception) -> AppError:
    """409 for a move the state machine or the pause refused (TransitionError / ProjectPaused)."""
    return AppError(409, "project_paused" if isinstance(exc, ProjectPaused) else "transition_refused", str(exc))


# The code of an error raised as a plain HTTPException (one no route has converted yet, or FastAPI's own), by status.
STATUS_CODES = {
    400: "bad_request",
    402: "budget_exceeded",
    404: "not_found",
    409: "conflict",
    422: "invalid_request",
    502: "provider_unavailable",
}


def body(status_code: int, detail: Any, code: str | None = None, params: dict[str, Any] | None = None) -> dict:
    return {"detail": detail, "code": code or STATUS_CODES.get(status_code, "error"), "params": params or {}}
