"""Every API error carries a stable `code` and `params` next to its English `detail` (app/errors.py), so the UI can say
it in the owner's language (web/i18n.js `err.<code>`); and every code the API can send has text in all three."""

import ast
import json
import re
from pathlib import Path

from app.main import app
from app.routers.manager import get_gateway
from tests.test_manager import use_fake

ROOT = Path(__file__).resolve().parent.parent


def test_not_found_names_the_resource(client):
    for path, resource in [("/projects/999", "project"), ("/users/999", "user"), ("/agents/nope", "agent"),
                           ("/skills/nope", "skill")]:
        r = client.get(path)
        assert r.status_code == 404, path
        body = r.json()
        assert body["code"] == "not_found" and isinstance(body["detail"], str)
        assert body["params"]["resource"] == resource
    r = client.post("/benchmarks/999/rate", json={"rating": 3})
    assert r.status_code == 404 and r.json()["params"]["resource"] == "benchmark_run"


def test_an_unknown_route_is_coded_too(client):
    r = client.get("/no-such-route")
    assert r.status_code == 404 and r.json()["code"] == "not_found"


def test_validation_errors_keep_their_detail_list(client):
    r = client.post("/users", json={})
    assert r.status_code == 422
    assert r.json()["code"] == "invalid_request" and isinstance(r.json()["detail"], list)


def test_transition_and_pause_refusals(client, project):
    pid = project["id"]
    t = client.post(f"/projects/{pid}/tasks", json={"title": "T"}).json()
    r = client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": "COMPLETED"})
    assert r.status_code == 409 and r.json()["code"] == "transition_refused"
    client.post(f"/projects/{pid}/pause", json={"paused": True})
    r = client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": "READY"})
    assert r.status_code == 409 and r.json()["code"] == "project_paused"


def test_budget_and_duplicate_email(client, session_factory, project):
    assert client.post("/users", json={"email": "a@example.com"}).json()["code"] == "email_taken"
    pid = project["id"]
    client.put(f"/settings/project/{pid}", json={"budget": {"project_usd": 0}})
    use_fake(client, session_factory, [{"understanding": "u", "steps": []}])
    r = client.post(f"/projects/{pid}/plan")
    assert r.status_code == 402 and r.json()["code"] == "budget_exceeded"
    app.dependency_overrides.pop(get_gateway, None)


def test_manager_refusal_is_coded(client, session_factory, project):
    use_fake(client, session_factory, [{"understanding": "u", "steps": []}])  # a plan with no steps
    r = client.post(f"/projects/{project['id']}/plan")
    assert r.status_code == 422 and r.json()["code"] == "request_refused"
    app.dependency_overrides.pop(get_gateway, None)


def _codes_in_app() -> set[str]:
    """Every code literal the backend can send: AppError(<status>, "<code>", ...), the refusal codes, STATUS_CODES."""
    from app import errors

    codes = set(errors.STATUS_CODES.values()) | {"project_paused", "transition_refused", "error"}
    for path in (ROOT / "app").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "AppError":
                code = node.args[1]
                options = [code.body, code.orelse] if isinstance(code, ast.IfExp) else [code]
                assert all(isinstance(o, ast.Constant) for o in options), f"{path}: an AppError code must be a literal"
                codes.update(o.value for o in options)
    return codes


def _ui_messages() -> dict[str, dict[str, str]]:
    text = (ROOT / "web" / "i18n.js").read_text(encoding="utf-8")
    match = re.search(r"/\*MESSAGES\*/(.*?)/\*END MESSAGES\*/", text, re.S)
    assert match, "web/i18n.js must keep its dictionaries between /*MESSAGES*/ and /*END MESSAGES*/"
    return json.loads(match.group(1))


def test_every_backend_code_has_text_in_every_language():
    messages = _ui_messages()
    for code in _codes_in_app():
        for locale in ("fa", "en", "tr"):
            assert f"err.{code}" in messages[locale], (locale, code)
