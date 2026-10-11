"""Building an agent with its owner: the adaptive intake and the versioned, approved brief (PR2 of issue #2;
docs/HUMAN_GUIDED_FACTORY.md §4–§5; owner decision d28).

Deterministic, no model call: which question comes next, when the brief is complete, what an approval covers and
whether a request still applies are all code. The questions live in registry/factory/brief.yaml.

- One main question per turn, in priority order; a known field is never asked again; "I don't know; suggest" puts a
  proposal (or an open question) instead of an answer. A vague answer is asked again; a contradiction (the web
  mentioned, yet the limits forbid internet access) becomes the next question.
- The brief is immutable versions (BriefRevision): every change is a new one. Each field is
  {value, status: draft|proposed|confirmed, source: user|suggestion|default|unknown, revision}; a guess never passes for
  the owner's answer.
- Approving binds to one exact version (Approval) and confirms every field in a new version; changing the brief after
  that invalidates the approval and goes back to the approval step. Silence approves nothing.
- Every change names the session revision it was made against (`expected_revision`): an older one is a 409, so a
  second tab or a late answer can't overwrite what it didn't see. A request id applies at most once (double click,
  retry after a lost reply). Every change writes a RunEvent with its actor.
"""

import copy
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import settings_layers
from app.errors import AppError, not_found
from app.events import record_factory_event
from app.models import (
    Approval, BriefRevision, FactoryRequest, FactorySession, FactoryStage, RunEvent, User, utcnow,
)
from app.preferences import DEFAULT_LOCALE, LOCALES

BRIEF_FILE = Path(__file__).resolve().parent.parent / "registry" / "factory" / "brief.yaml"
MAX_ANSWER_CHARS = 4000
S = FactoryStage
# A stage in which the brief can still change by answering (a change after approval reopens it).
EDITABLE = {S.DRAFT, S.CLARIFYING, S.AWAITING_BRIEF_APPROVAL, S.DESIGNING}
TERMINAL = {S.CANCELLED, S.ACTIVE}

# Answers that say nothing: asked again, never taken as the owner's answer.
VAGUE = {
    "نمیدونم", "نمی‌دونم", "نمی‌دانم", "نمیدانم", "هرچی", "هر چی", "هیچی", "چیزی", "idk", "dunno", "whatever",
    "anything", "nothing", "something", "test", "asdf", "bilmiyorum", "herhangi", "fark etmez", "?", "...", "-", "x",
    "hm", "hmm", "ok", "okay", "باشه", "اوکی", "tamam",
}
WEB_WORDS = re.compile(r"\b(web|internet|online|google|website|search)\b|اینترنت|وب|سایت|جست‌?وجو|جستجو|internet|çevrimiçi",
                       re.IGNORECASE)


# ---------- the question bank ----------


@lru_cache(maxsize=1)
def field_specs() -> tuple[dict[str, Any], ...]:
    doc = yaml.safe_load(BRIEF_FILE.read_text(encoding="utf-8"))
    return tuple(doc["fields"])


def spec(field: str) -> dict[str, Any]:
    for s in field_specs():
        if s["field"] == field:
            return s
    raise AppError(422, "unknown_field", f"Unknown brief field '{field}'", field=field)


def _text(localized: dict[str, str] | None, locale: str) -> str | None:
    if not localized:
        return None
    return localized.get(locale) or localized.get("en") or next(iter(localized.values()))


# ---------- reading a session ----------


def load_session(db: Session, session_id: int, owner: User, *, lock: bool = False) -> FactorySession:
    """The owner's session; another account's is the same 404 as a missing one. `lock` serializes the changes to one
    session (PostgreSQL row lock; SQLite's single write lock does the same once the first write happens)."""
    query = select(FactorySession).where(FactorySession.id == session_id)
    if lock:
        query = query.with_for_update()
    fs = db.scalar(query)
    if fs is None or fs.owner_id != owner.id:
        raise not_found("factory_session")
    return fs


def current_brief(db: Session, fs: FactorySession) -> BriefRevision:
    return db.scalar(
        select(BriefRevision).where(BriefRevision.session_id == fs.id).order_by(BriefRevision.version.desc()).limit(1)
    )


def active_approval(db: Session, fs: FactorySession) -> Approval | None:
    return db.scalar(
        select(Approval).where(Approval.session_id == fs.id, Approval.invalidated_at.is_(None))
        .order_by(Approval.id.desc()).limit(1)
    )


def is_vague(field: str, value: str) -> bool:
    text = value.strip().lower().strip(".!؟?")
    if text in VAGUE or len(text) < 2:
        return True
    return field == "goal" and len(text.split()) < 3  # "what, for whom, what it does" needs a sentence


def conflicts(fields: dict[str, Any]) -> list[dict[str, str]]:
    """Contradictions between answers, each turned into a question. The web mentioned in what the agent does or uses,
    while the limits (still the default, not the owner's own words) forbid internet access."""
    found = []
    mentioned = " ".join(str((fields.get(k) or {}).get("value") or "") for k in ("goal", "expected_outputs", "allowed_sources", "input_examples"))
    limits = fields.get("constraints") or {}
    if WEB_WORDS.search(mentioned) and limits.get("source") == "default":
        found.append({"code": "internet_vs_constraints", "field": "constraints"})
    return found


def next_question(fields: dict[str, Any]) -> dict[str, str] | None:
    """The next thing to ask: an unanswered field (in priority order), one whose answer was too vague, then a
    contradiction. None when there is nothing left to ask."""
    for s in field_specs():
        if not s.get("asked"):
            continue
        f = fields.get(s["field"])
        if f is None:
            return {"field": s["field"], "kind": "ask"}
        if f.get("unclear"):
            return {"field": s["field"], "kind": "clarify"}
    for c in conflicts(fields):
        return {"field": c["field"], "kind": "conflict", "conflict": c["code"]}
    return None


def missing_critical(fields: dict[str, Any]) -> list[str]:
    return [s["field"] for s in field_specs()
            if s.get("critical") and (not (fields.get(s["field"]) or {}).get("value") or (fields.get(s["field"]) or {}).get("unclear"))]


# ---------- changing a session ----------


def _new_version(db: Session, fs: FactorySession, fields: dict[str, Any], actor: User) -> BriefRevision:
    latest = db.scalar(select(func.max(BriefRevision.version)).where(BriefRevision.session_id == fs.id)) or 0
    revision = BriefRevision(session_id=fs.id, version=latest + 1, fields=fields, created_by=actor.id)
    db.add(revision)
    return revision


def _claim_request(db: Session, owner: User, request_id: str, session_id: int | None) -> bool:
    """True if this request id is new (and now taken); False if it was already applied."""
    if db.scalar(select(FactoryRequest.id).where(FactoryRequest.owner_id == owner.id,
                                                 FactoryRequest.request_id == request_id)):
        return False
    db.add(FactoryRequest(owner_id=owner.id, request_id=request_id, session_id=session_id))
    return True


def _commit(db: Session) -> bool:
    """Commit; False if a concurrent request with the same id won the race (nothing of this one is kept)."""
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def _check(fs: FactorySession, expected_revision: int, action: str) -> None:
    if fs.revision != expected_revision:
        raise AppError(409, "stale_revision", "This build changed since you loaded it; reload and look again",
                       current_revision=fs.revision, expected_revision=expected_revision)
    if fs.stage == S.PAUSED.value and action not in ("resume", "cancel"):
        raise AppError(409, "session_paused", "This build is paused; resume it first")
    if fs.stage in {s.value for s in TERMINAL}:
        raise AppError(409, "stage_not_allowed", f"Nothing can change once the build is {fs.stage}",
                       stage=fs.stage, action=action)


def _bump(fs: FactorySession, stage: FactoryStage | None = None) -> None:
    fs.revision += 1
    fs.updated_at = utcnow()
    if stage is not None:
        fs.stage = stage.value


def create_session(db: Session, owner: User, *, title: str, locale: str, request_id: str) -> FactorySession:
    """A new build: the brief starts with the proposals for what isn't asked (in the owner's language)."""
    existing = db.scalar(select(FactoryRequest).where(FactoryRequest.owner_id == owner.id,
                                                      FactoryRequest.request_id == request_id))
    if existing is not None and existing.session_id is not None:
        return load_session(db, existing.session_id, owner)
    resolved = settings_layers.resolve(db)
    locale = locale if locale in LOCALES else DEFAULT_LOCALE
    fs = FactorySession(
        owner_id=owner.id, title=title, locale=locale,
        # Building an agent is step by step unless the owner chooses otherwise, and never looser than the ceiling.
        interaction_policy=settings_layers.strictest(settings_layers.FACTORY_DEFAULT_POLICY,
                                                     resolved["max_interaction_policy"]),
        quality_target=resolved["quality_target"], stage=S.DRAFT.value, revision=1,
    )
    db.add(fs)
    db.flush()
    fields = {
        s["field"]: {"value": _text(s["default"], locale), "status": "proposed", "source": "default", "revision": 1}
        for s in field_specs() if s.get("default")
    }
    _new_version(db, fs, fields, owner)
    db.add(FactoryRequest(owner_id=owner.id, request_id=request_id, session_id=fs.id))
    record_factory_event(db, fs.id, "factory.created", owner.id, title=title, locale=locale,
                         interaction_policy=fs.interaction_policy)
    if not _commit(db):  # the same create, twice at once: return the one that landed
        existing = db.scalar(select(FactoryRequest).where(FactoryRequest.owner_id == owner.id,
                                                          FactoryRequest.request_id == request_id))
        return load_session(db, existing.session_id, owner)
    return fs


def answer(db: Session, owner: User, session_id: int, *, field: str, value: str | None, dont_know: bool,
           expected_revision: int, request_id: str) -> tuple[FactorySession, bool]:
    """Set one field (an answer, an edit, or "I don't know; suggest"). Returns (session, applied)."""
    fs = load_session(db, session_id, owner, lock=True)
    if not _claim_request(db, owner, request_id, fs.id):
        db.rollback()
        return fs, False
    _check(fs, expected_revision, "answer")
    if S(fs.stage) not in EDITABLE:
        raise AppError(409, "stage_not_allowed", f"The brief can't change while the build is {fs.stage}",
                       stage=fs.stage, action="answer")
    s = spec(field)
    brief = current_brief(db, fs)
    fields = copy.deepcopy(brief.fields)
    entry: dict[str, Any] = {"revision": brief.version + 1}
    if dont_know:
        if s["field"] == "goal":
            raise AppError(422, "answer_required", "Only you can say what to build", field=field)
        suggestion = _text(s.get("suggestion"), fs.locale)
        entry.update({"value": suggestion, "status": "proposed", "source": "suggestion" if suggestion else "unknown"})
    else:
        text = (value or "").strip()
        if not text:
            raise AppError(422, "answer_required", "Write an answer, or choose \"I don't know\"", field=field)
        if len(text) > MAX_ANSWER_CHARS:
            raise AppError(422, "answer_too_long", f"At most {MAX_ANSWER_CHARS} characters", field=field,
                           limit=MAX_ANSWER_CHARS)
        entry.update({"value": text, "status": "draft", "source": "user"})
        if is_vague(field, text):
            entry["unclear"] = True
    fields[field] = entry
    _new_version(db, fs, fields, owner)

    was = S(fs.stage)
    invalidated = None
    if was is S.DESIGNING and (approval := active_approval(db, fs)) is not None:
        approval.invalidated_at, approval.invalidated_reason = utcnow(), "brief_changed"
        invalidated = approval.id
    complete = next_question(fields) is None
    _bump(fs, S.AWAITING_BRIEF_APPROVAL if complete else S.CLARIFYING)
    record_factory_event(db, fs.id, "factory.answered", owner.id, field=field, dont_know=dont_know,
                         unclear=bool(entry.get("unclear")), brief_version=brief.version + 1,
                         **({"from": was.value, "to": fs.stage} if was.value != fs.stage else {}),
                         **({"approval_invalidated": invalidated} if invalidated else {}))
    return fs, _commit(db)


def approve(db: Session, owner: User, session_id: int, *, brief_version: int, expected_revision: int,
            request_id: str) -> tuple[FactorySession, bool]:
    """The owner approves exactly brief `brief_version`: every field becomes confirmed in a new version, which the
    approval names. Refused for any other version, while something critical is missing or contradictory, or outside
    the approval step."""
    fs = load_session(db, session_id, owner, lock=True)
    if not _claim_request(db, owner, request_id, fs.id):
        db.rollback()
        return fs, False
    _check(fs, expected_revision, "approve")
    if fs.stage != S.AWAITING_BRIEF_APPROVAL.value:
        raise AppError(409, "stage_not_allowed", f"Nothing to approve while the build is {fs.stage}",
                       stage=fs.stage, action="approve")
    brief = current_brief(db, fs)
    if brief.version != brief_version:
        raise AppError(409, "stale_version", "The brief changed since you read it; look at it again",
                       current_version=brief.version)
    if missing := missing_critical(brief.fields):
        raise AppError(409, "brief_incomplete", "Some essential answers are missing", missing=missing)
    if found := conflicts(brief.fields):
        raise AppError(409, "brief_conflict", "Some answers contradict each other", conflicts=[c["code"] for c in found])
    confirmed = {k: {**v, "status": "confirmed"} for k, v in brief.fields.items()}
    approved = _new_version(db, fs, confirmed, owner)
    db.add(Approval(session_id=fs.id, actor_id=owner.id, stage=S.AWAITING_BRIEF_APPROVAL.value, resource="brief",
                    resource_version=approved.version, scope=sorted(confirmed)))
    _bump(fs, S.DESIGNING)
    record_factory_event(db, fs.id, "factory.brief_approved", owner.id, brief_version=approved.version,
                         reviewed_version=brief_version)
    return fs, _commit(db)


def request_changes(db: Session, owner: User, session_id: int, *, note: str | None, expected_revision: int,
                    request_id: str) -> tuple[FactorySession, bool]:
    fs = load_session(db, session_id, owner, lock=True)
    if not _claim_request(db, owner, request_id, fs.id):
        db.rollback()
        return fs, False
    _check(fs, expected_revision, "request_changes")
    if fs.stage not in (S.AWAITING_BRIEF_APPROVAL.value, S.DESIGNING.value):
        raise AppError(409, "stage_not_allowed", f"Nothing to send back while the build is {fs.stage}",
                       stage=fs.stage, action="request_changes")
    if (approval := active_approval(db, fs)) is not None:
        approval.invalidated_at, approval.invalidated_reason = utcnow(), "changes_requested"
    was = fs.stage
    _bump(fs, S.CLARIFYING)
    record_factory_event(db, fs.id, "factory.changes_requested", owner.id, note=note, **{"from": was})
    return fs, _commit(db)


def set_paused(db: Session, owner: User, session_id: int, *, paused: bool, expected_revision: int,
               request_id: str) -> tuple[FactorySession, bool]:
    fs = load_session(db, session_id, owner, lock=True)
    if not _claim_request(db, owner, request_id, fs.id):
        db.rollback()
        return fs, False
    _check(fs, expected_revision, "pause" if paused else "resume")
    if paused:
        if fs.stage == S.PAUSED.value:
            raise AppError(409, "stage_not_allowed", "Already paused", stage=fs.stage, action="pause")
        fs.stage_before_pause = fs.stage
        _bump(fs, S.PAUSED)
    else:
        if fs.stage != S.PAUSED.value:
            raise AppError(409, "stage_not_allowed", "Not paused", stage=fs.stage, action="resume")
        _bump(fs, S(fs.stage_before_pause or S.CLARIFYING.value))
        fs.stage_before_pause = None
    record_factory_event(db, fs.id, "factory.paused" if paused else "factory.resumed", owner.id)
    return fs, _commit(db)


def cancel(db: Session, owner: User, session_id: int, *, expected_revision: int, request_id: str
           ) -> tuple[FactorySession, bool]:
    fs = load_session(db, session_id, owner, lock=True)
    if not _claim_request(db, owner, request_id, fs.id):
        db.rollback()
        return fs, False
    _check(fs, expected_revision, "cancel")
    was = fs.stage
    _bump(fs, S.CANCELLED)
    record_factory_event(db, fs.id, "factory.cancelled", owner.id, **{"from": was})
    return fs, _commit(db)


# ---------- what the UI shows ----------


def _question_view(q: dict[str, str] | None, locale: str, level: str) -> dict[str, Any] | None:
    if q is None:
        return None
    s = spec(q["field"])
    help_lines = [_text(s.get("example"), locale)]
    if level in ("professional", "developer"):
        help_lines.insert(0, _text(s.get("why"), locale))
    view = {
        "field": q["field"], "kind": q["kind"], "text": _text(s.get("text"), locale),
        "help": [h for h in help_lines if h], "can_suggest": q["field"] != "goal",
        "suggestion": _text(s.get("suggestion"), locale),
    }
    if q["kind"] == "conflict":
        view["conflict"] = q["conflict"]
    if level == "developer":
        view["field_key"] = q["field"]
    return view


def state(db: Session, fs: FactorySession, *, locale: str, level: str, duplicate: bool = False) -> dict[str, Any]:
    brief = current_brief(db, fs)
    fields = brief.fields
    approval = active_approval(db, fs)
    question = next_question(fields) if S(fs.stage) in (S.DRAFT, S.CLARIFYING) else None
    order = [s["field"] for s in field_specs()]
    labels = {s["field"]: _text(s.get("text"), locale) for s in field_specs()}

    def row(name: str) -> dict[str, Any]:
        return {"field": name, "question": labels.get(name), **fields[name]}

    present = [n for n in order if n in fields]
    events = db.scalars(select(RunEvent).where(RunEvent.factory_session_id == fs.id)
                        .order_by(RunEvent.id.desc()).limit(20)).all()
    asked = [s for s in field_specs() if s.get("asked")]
    return {
        "session": {
            "id": fs.id, "title": fs.title, "stage": fs.stage, "revision": fs.revision, "locale": fs.locale,
            "interaction_policy": fs.interaction_policy, "quality_target": fs.quality_target,
            "created_at": fs.created_at, "updated_at": fs.updated_at,
        },
        "brief": {"version": brief.version, "fields": fields},
        "question": _question_view(question, locale, level),
        "progress": {"answered": sum(1 for s in asked if s["field"] in fields and not fields[s["field"]].get("unclear")),
                     "total": len(asked)},
        "summary": {
            "from_you": [row(n) for n in present if fields[n]["source"] == "user"],
            "proposals": [row(n) for n in present if fields[n]["source"] in ("default", "suggestion")],
            "unknowns": [row(n) for n in present if fields[n]["source"] == "unknown"],
            "missing": missing_critical(fields),
            "conflicts": [c["code"] for c in conflicts(fields)],
        },
        "can_approve": (fs.stage == S.AWAITING_BRIEF_APPROVAL.value and not missing_critical(fields)
                        and not conflicts(fields)),
        "approval": None if approval is None else {
            "id": approval.id, "resource": approval.resource, "version": approval.resource_version,
            "actor_id": approval.actor_id, "decided_at": approval.decided_at,
        },
        "events": [{"type": e.type, "payload": e.payload, "created_at": e.created_at} for e in events],
        "duplicate": duplicate,
    }


def list_sessions(db: Session, owner: User) -> list[FactorySession]:
    return list(db.scalars(select(FactorySession).where(FactorySession.owner_id == owner.id)
                           .order_by(FactorySession.updated_at.desc())))
