"""First-run help inside the app (the "Setup" page): what this server has for reaching a model, and a test the admin can
run on purpose. It reports booleans and names only — never a token, key or secret — and says nothing about other
accounts. The test is the only part that makes a model call (one tiny one, through the gateway, logged like any other)."""

import os
import shutil
import time
import urllib.error
import urllib.request
import json
from typing import Any

from sqlalchemy.orm import Session

from app import credentials, settings_layers
from app.config import DATABASE_URL
from app.errors import AppError
from app.gateway.providers import ProviderError
from app.gateway.service import BudgetExceeded, Gateway
from app.models import User

TEST_ROLE = "cheap"
TEST_PROMPT = "Reply with the single word OK."
TEST_GAP_SECONDS = 10.0
_last_test = 0.0


def _ollama(base_url: str, wanted: set[str]) -> dict[str, Any]:
    """Is the Ollama server answering, and which of the configured models does it have? (2 s, the operator's own URL.)"""
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=2) as response:
            names = sorted(m.get("name", "") for m in json.loads(response.read()).get("models", []))
    except (urllib.error.URLError, OSError, ValueError):
        return {"url": base_url, "reachable": False, "models": [], "missing": sorted(wanted)}
    have = set(names) | {n.split(":")[0] for n in names}
    return {"url": base_url, "reachable": True, "models": names, "missing": sorted(w for w in wanted if w not in have)}


def status(db: Session, me: User) -> dict[str, Any]:
    settings = settings_layers.resolve(db)
    access = settings.get("model_access")
    models = {role: {"provider": cfg.get("provider"), "model": cfg.get("model")} for role, cfg in settings["models"].items()}
    out: dict[str, Any] = {
        "is_admin": bool(me.is_admin),
        "server": {"database": "postgresql" if DATABASE_URL.startswith("postgres") else "sqlite",
                   "in_docker": os.path.exists("/.dockerenv"), "workers": int(os.getenv("AGENT_FACTORY_WORKERS", "1") or 0),
                   "behind_proxy": os.getenv("AGENT_FACTORY_BEHIND_PROXY") == "1"},
        "keys": {"available": credentials.status() is None, "reason": credentials.status()},
        "model": {
            "access": access, "roles": models,
            "claude": {"cli": shutil.which("claude") is not None, "token": bool(os.getenv("CLAUDE_CODE_OAUTH_TOKEN"))},
            "anthropic_key": bool(os.getenv("ANTHROPIC_API_KEY")),
            "openai_key": bool(os.getenv("OPENAI_API_KEY")),
            "connections": [c["provider"] for c in credentials.listing(db, me)["items"]],
            "ollama": None,
        },
    }
    if access == "ollama":
        wanted = {cfg["model"] for cfg in models.values() if cfg.get("provider") == "ollama" and cfg.get("model")}
        out["model"]["ollama"] = _ollama((os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/"), wanted)
    return out


def test_model(db: Session, gateway: Gateway, me: User, role: str = TEST_ROLE) -> dict[str, Any]:
    """One tiny call, on the admin's click, through the gateway with the server's own settings: whatever came back,
    in plain terms. At most one every few seconds."""
    global _last_test
    if not me.is_admin:
        raise AppError(403, "forbidden", "Not allowed: admin_only", reason="admin_only")
    now = time.monotonic()
    if now - _last_test < TEST_GAP_SECONDS:
        raise AppError(429, "too_many_attempts", "Wait a few seconds between tests")
    _last_test = now
    try:
        response = gateway.call(role, system="You answer in one word.", user=TEST_PROMPT, agent="setup")
    except (ProviderError, BudgetExceeded) as e:
        return {"ok": False, "role": role, "error": str(e)[:400]}
    route = settings_layers.resolve(db)["models"].get(role, {})
    return {"ok": True, "role": role, "provider": route.get("provider"), "model": route.get("model"),
            "reply": (response.text or "").strip()[:80], "duration_ms": response.duration_ms,
            "cost_usd": response.cost_usd}
