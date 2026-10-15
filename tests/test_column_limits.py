"""Every String(n) column: who writes it, and what refuses a value that doesn't fit before the store.

PostgreSQL (production) refuses a string longer than its column; SQLite (the tests') takes it. So a column nobody guards
passes every test and fails only in production: a 500 at the store, after the paid model call when the text came from a
model, and again, paid again, on every retry. The case that found this: app/memory.py copies a task's title (up to 300
characters) into learning_traces.concept, which held 200.

tests/postgres_limits.py makes every test's SQLite refuse what PostgreSQL refuses. This file is the audit on top of it:
each column below is accounted for, so a column added later fails `test_every_column_is_audited` until its writers are
classified; and for each field a request body or a model reply can carry into a column, the value of exactly the
column's length is stored (the refusing engine proves the column takes it) while one more character is refused first,
as a 422 naming the field. FakeProvider only.
"""

import ast
import json
import re
from collections import namedtuple
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import Integer, select

import app as app_package
from app.chat import ChatReplyOut
from app.db import Base, varchar_length
from app.gateway.providers import FakeProvider
from app.gateway.service import Gateway, default_providers
from app.main import app as api
from app.manager import PlanStepIn
from app.models import Agent, BudgetHold, Feedback, SettingsLayer, SettingsScope, Skill, Task
from app.registry import MODEL_ROLES, RegistryError, parse_skill_file
from app.routers.manager import get_gateway
from app.schemas import TaskCreate
from app.storable import INT32_MAX, StorableIn, limit
from tests.test_project_pause import use_provider
from tests.test_task_run import RUN_RESULT, use_fake_role

Ctx = namedtuple("Ctx", "uid pid")  # the owner and the project every request below belongs to
JSON_HEADERS = {"content-type": "application/json"}


def send(client, method, path, body):
    """The body as raw JSON text: an ASCII-escaped lone surrogate or a bare NaN, which httpx's own encoder refuses."""
    return client.request(method, path, content=json.dumps(body), headers=JSON_HEADERS)


def limited_columns() -> dict[tuple[str, str], int]:
    return {
        (table.name, column.name): length
        for table in Base.metadata.sorted_tables for column in table.columns
        if (length := varchar_length(column.type))
    }


def _step(**kw):
    return {"title": "t", "agent": "researcher", "goal": "g", "depends_on": [], "risk": "low", **kw}


# ---------- the audit: every String(n) column, by who writes it ----------

# Written from an API request body: column -> (the request that carries the value, the field a 422 names).
API = {
    ("users", "email"): (lambda c, x, v: send(c, "POST", "/users", {"email": v}), "email"),
    ("users", "name"): (lambda c, x, v: send(c, "POST", "/users", {"email": "n@example.com", "name": v}), "name"),
    ("workspaces", "name"): (lambda c, x, v: send(c, "POST", "/workspaces", {"name": v}), "name"),
    ("projects", "title"): (
        lambda c, x, v: send(c, "POST", "/projects", {"owner_id": x.uid, "title": v, "goal": "g"}), "title"),
    ("tasks", "title"): (lambda c, x, v: send(c, "POST", f"/projects/{x.pid}/tasks", {"title": v}), "title"),
    ("tasks", "owner"): (
        lambda c, x, v: send(c, "POST", f"/projects/{x.pid}/tasks", {"title": "t", "owner": v}), "owner"),
    ("feedback", "agent"): (
        lambda c, x, v: send(c, "POST", "/feedback", {"project_id": x.pid, "agent": v, "rating": "up"}), "agent"),
    ("memory_items", "title"): (
        lambda c, x, v: send(c, "POST", f"/projects/{x.pid}/memory", {"category": "brief", "title": v, "content": "c"}),
        "title"),
    # The benchmark's rows hold its route; "fake" is the provider the tests register, "nowhere" one nobody does.
    ("benchmark_runs", "provider"): (
        lambda c, x, v: send(c, "POST", "/benchmarks/run", {"routes": [{"provider": v, "model": "m"}]}), "provider"),
    ("benchmark_runs", "model"): (
        lambda c, x, v: send(c, "POST", "/benchmarks/run", {"routes": [{"provider": "fake", "model": v}]}), "model"),
    ("benchmark_runs", "effort"): (
        lambda c, x, v: send(c, "POST", "/benchmarks/run", {"routes": [{"provider": "fake", "model": "m", "effort": v}]}),
        "effort"),
    # A settings layer's model id is what every call is logged with (model_calls.model), after the call.
    ("model_calls", "model"): (
        lambda c, x, v: send(c, "PUT", "/settings/global/0", {"models": {"manager": {"provider": "fake", "model": v}}}),
        "model"),
    ("agents", "name"): (
        lambda c, x, v: send(c, "POST", "/agents", {
            "name": v, "description": "d", "model_role": "research", "capabilities": ["column-limits-audit"]}),
        "name"),
}

# Written from a model's reply: column -> a check that a value of that length is valid / invalid in the reply's class.
MODEL = {
    ("tasks", "title"): lambda v: PlanStepIn.model_validate(_step(title=v)),
    ("tasks", "owner"): lambda v: PlanStepIn.model_validate(_step(agent=v)),
}

# A copy of another column's value (written after the paid run that produced it): never narrower than its source.
DERIVED = {
    ("memory_items", "title"): ("tasks", "title"),
    ("learning_traces", "concept"): ("tasks", "title"),
}

# Written from files in registry/ at startup: a longer value is a RegistryError naming the file, not a database error.
REGISTRY = {("skills", "name"), ("skills", "summary")}

# Written only from values the code itself defines (a closed set): the longest of them fits.
CLOSED = {
    ("agents", "model_role"): lambda: MODEL_ROLES,  # AgentSpec refuses any other
    ("model_calls", "role"): lambda: MODEL_ROLES | {"manager"},
    ("model_calls", "provider"): lambda: {p.name for p in default_providers().values()},  # the provider's own name
    ("model_calls", "agent"): lambda: {"manager", "benchmark"},  # or a registry agent's name: AgentSpec's pattern
    ("feedback", "rating"): lambda: {"up", "down"},  # FeedbackIn's pattern
    ("chat_messages", "role"): lambda: {"user", "manager"},
    ("chat_messages", "suggested_action"): lambda: {"none", "plan", "approve", "checkpoint:" + "9" * 12},
}

CODE = {("run_events", "type")}  # the literal passed to record_event: test_every_event_type_fits_its_column
NOT_WRITTEN = {("learning_traces", "mastery_signal")}  # no writer yet: test_nothing_writes_the_unwritten_columns

AUDITED = set(API) | set(MODEL) | set(DERIVED) | REGISTRY | set(CLOSED) | CODE | NOT_WRITTEN


def test_every_column_is_audited():
    """A String(n) column added to the models fails here until its writers are classified above (and tested below)."""
    limited = set(limited_columns())
    assert limited - AUDITED == set(), "classify the writers of these columns in tests/test_column_limits.py"
    assert AUDITED - limited == set(), "no longer a String(n) column: drop them from the audit"


def test_a_copy_is_never_narrower_than_its_source():
    """The proven case: the copy was refused by PostgreSQL, after the paid run."""
    lengths = limited_columns()
    for copy, source in DERIVED.items():
        assert lengths[copy] >= lengths[source], f"{copy} holds a copy of {source}"


# ---------- request bodies: exactly n is stored, one more is a 422 naming the field ----------


@pytest.fixture
def ctx(client, project, session_factory):
    use_provider(client, session_factory, FakeProvider())  # the benchmark's routes; its calls cost nothing here
    return Ctx(uid=project["owner_id"], pid=project["id"])


def _names(detail, field):
    """Does a 422's detail name `field`: a list of {loc, msg}, or text (the settings route's one line, an agent's
    pydantic report) where the field stands as a word of its own, not inside another ("versioned")."""
    if isinstance(detail, list):
        return any(field in map(str, e["loc"]) for e in detail)
    return re.search(rf"(^|[\s.]){re.escape(field)}(?=[\s:]|$)", detail, re.M) is not None


@pytest.mark.parametrize("column", sorted(API), ids=lambda c: ".".join(c))
def test_the_longest_value_is_stored(client, ctx, column):
    request, _ = API[column]
    r = request(client, ctx, "x" * limited_columns()[column])
    assert r.status_code in (200, 201), r.text  # through the engine that refuses what PostgreSQL refuses


@pytest.mark.parametrize("column", sorted(API), ids=lambda c: ".".join(c))
def test_one_character_more_is_a_422_naming_the_field(client, ctx, column):
    request, field = API[column]
    n = limited_columns()[column]
    r = request(client, ctx, "x" * (n + 1))
    assert r.status_code == 422, r.text
    assert _names(r.json()["detail"], field), r.text
    assert str(n) in json.dumps(r.json()["detail"])  # says the limit, so the owner knows how much to cut


@pytest.mark.parametrize("poison", ["a\x00b", "a \ud83d b"], ids=["nul", "lone_surrogate"])
@pytest.mark.parametrize("column", sorted(API), ids=lambda c: ".".join(c))
def test_text_the_database_cannot_store_is_a_422_naming_the_field(client, ctx, column, poison):
    """PostgreSQL text can't hold a NUL and no database encodes a lone surrogate: 422, not a 500 at the store."""
    request, field = API[column]
    r = request(client, ctx, poison)
    assert r.status_code == 422, r.text
    assert _names(r.json()["detail"], field), r.text


# What lands in a Text column or a JSON document (no length): the same two refusals, and NaN/Infinity for JSON.
TEXT_FIELDS = {
    "projects.goal": (lambda c, x, v: send(c, "POST", "/projects", {"owner_id": x.uid, "title": "t", "goal": v}), "goal"),
    "memory_items.content": (
        lambda c, x, v: send(c, "POST", f"/projects/{x.pid}/memory", {"category": "brief", "title": "t", "content": v}),
        "content"),
    "feedback.note": (
        lambda c, x, v: send(c, "POST", "/feedback", {"project_id": x.pid, "agent": "a", "rating": "up", "note": v}),
        "note"),
    "agents.description": (
        lambda c, x, v: send(c, "POST", "/agents", {
            "name": "texty", "description": v, "model_role": "research", "capabilities": ["a"]}), "description"),
    "agents.instructions": (
        lambda c, x, v: send(c, "POST", "/agents", {
            "name": "texty", "description": "d", "model_role": "research", "capabilities": ["a"], "instructions": v}),
        "instructions"),
    "tasks.input": (lambda c, x, v: send(c, "POST", f"/projects/{x.pid}/tasks", {"title": "t", "input": {"k": [v]}}), "input"),
    "task transition output": (
        lambda c, x, v: send(c, "POST", f"/projects/{x.pid}/tasks/1/transition", {"status": "READY", "output": {"k": v}}),
        "output"),
    "plan reject feedback": (lambda c, x, v: send(c, "POST", f"/projects/{x.pid}/plan/reject", {"feedback": v}), "feedback"),
    "decision note": (
        lambda c, x, v: send(c, "POST", f"/projects/{x.pid}/tasks/1/decide", {"option": 0, "note": v}), "note"),
}


@pytest.mark.parametrize("poison", ["a\x00b", "a \ud83d b"], ids=["nul", "lone_surrogate"])
@pytest.mark.parametrize("name", sorted(TEXT_FIELDS))
def test_unstorable_text_in_any_text_field_is_a_422_before_anything_is_spent(client, ctx, name, poison):
    request, field = TEXT_FIELDS[name]
    r = request(client, ctx, poison)
    assert r.status_code == 422, r.text
    assert _names(r.json()["detail"], field), r.text


def test_a_lone_surrogate_in_a_refused_input_is_echoed_without_loss(client):
    """pydantic refuses it in any str field and the 422 echoes the input: as ASCII-escaped JSON, which carries it (a
    UTF-8 body can't, so this used to be a 500) and reads back as the text that was sent."""
    r = send(client, "POST", "/users", {"email": "a \ud83d b"})
    assert r.status_code == 422 and r.content.isascii()
    assert r.json()["detail"][0]["input"] == "a \ud83d b"


NOT_FINITE = {
    "tasks.input": (lambda c, x, v: send(c, "POST", f"/projects/{x.pid}/tasks", {"title": "t", "input": {"k": v}}), "input"),
    "task transition output": (
        lambda c, x, v: send(c, "POST", f"/projects/{x.pid}/tasks/1/transition", {"status": "READY", "output": {"k": [v]}}),
        "output"),
    "agents.permissions": (
        lambda c, x, v: send(c, "POST", "/agents", {
            "name": "texty", "description": "d", "model_role": "research", "capabilities": ["a"], "permissions": {"n": v}}),
        "permissions"),
}


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "-inf"])
@pytest.mark.parametrize("name", sorted(NOT_FINITE))
def test_a_json_field_with_nan_or_infinity_is_a_422(client, ctx, name, number):
    """PostgreSQL's json type refuses both (Python writes them as bare tokens): a 500 at the store otherwise."""
    request, field = NOT_FINITE[name]
    r = request(client, ctx, number)
    assert r.status_code == 422, r.text
    assert _names(r.json()["detail"], field), r.text


def _nested(depth, leaf):
    document = cursor = {}
    for _ in range(depth):
        cursor["k"] = {}
        cursor = cursor["k"]
    cursor["k"] = leaf
    return document


def test_a_json_field_nested_deeper_than_python_recurses_is_still_checked():
    """The check walks the document without recursing: a deep body is refused or accepted on its content, not a
    RecursionError (a 500) from the check itself."""
    TaskCreate(title="t", input=_nested(5000, "fine"))
    for leaf, kind in ((float("nan"), "not_finite"), ("a\x00b", "unstorable_text")):
        with pytest.raises(ValidationError) as refused:
            TaskCreate(title="t", input=_nested(5000, leaf))
        assert [e["type"] for e in refused.value.errors(include_input=False)] == [kind]  # (nothing renders the input)


@pytest.mark.parametrize("key", ["a\x00b", "a \ud83d b"], ids=["nul", "lone_surrogate"])
def test_a_key_inside_a_json_field_is_checked_like_a_value(key):
    """A nested key with a lone surrogate would be stored (escaped) and then fail the encoding of every later read."""
    with pytest.raises(ValidationError, match="NUL|lone surrogate"):
        TaskCreate(title="t", input={"outer": [{key: 1}]})


# ---------- integers: the same class, 32 bits on PostgreSQL ----------

INTEGERS = {
    "tasks.priority": (lambda c, x, v: send(c, "POST", f"/projects/{x.pid}/tasks", {"title": "t", "priority": v}), "priority"),
    "tasks.max_retries": (
        lambda c, x, v: send(c, "POST", f"/projects/{x.pid}/tasks", {"title": "t", "max_retries": v}), "max_retries"),
    "agents.version": (  # (a name of its own per request: a repeated one is a 422 too, for another reason)
        lambda c, x, v: send(c, "POST", "/agents", {
            "name": f"v{v}", "description": "d", "model_role": "research", "capabilities": [f"c{v}"], "version": v}),
        "version"),
}


@pytest.mark.parametrize("name", sorted(INTEGERS))
def test_an_integer_that_fits_32_bits_is_stored_and_one_more_is_a_422(client, ctx, name):
    request, field = INTEGERS[name]
    assert request(client, ctx, INT32_MAX).status_code == 201
    r = request(client, ctx, INT32_MAX + 1)
    assert r.status_code == 422 and _names(r.json()["detail"], field), r.text


@pytest.mark.parametrize("name, value", [
    ("tasks.priority", 0), ("tasks.priority", -5), ("tasks.max_retries", 0), ("agents.version", 0), ("agents.version", -1),
])
def test_the_bounds_cut_only_what_the_database_cannot_hold(client, ctx, name, value):
    """Not a product rule: what was accepted before and PostgreSQL stores (zero, negatives) still is."""
    request, _ = INTEGERS[name]
    assert request(client, ctx, value).status_code == 201


def test_every_integer_column_is_accounted_for():
    """Integer columns that aren't keys: a request body's are bounded above, the rest are counted by the code."""
    from_api = {("tasks", "priority"), ("tasks", "max_retries"), ("agents", "version")}
    from_files = {("skills", "version")}  # SkillSpec
    counted = {  # by the code or reported by a provider (tokens, durations): nowhere near 2**31
        ("tasks", "retries"), ("settings_layers", "scope_id"),  # scope_id: an existing project/task/workspace's id, or 0
        ("benchmark_runs", "input_tokens"), ("benchmark_runs", "output_tokens"), ("benchmark_runs", "duration_ms"),
        ("benchmark_runs", "rating"),  # 1 to 5
        ("model_calls", "input_tokens"), ("model_calls", "output_tokens"), ("model_calls", "cache_read_tokens"),
        ("model_calls", "cache_write_tokens"), ("model_calls", "web_searches"), ("model_calls", "duration_ms"),
    }
    integers = {
        (table.name, column.name)
        for table in Base.metadata.sorted_tables for column in table.columns
        if isinstance(column.type, Integer) and not column.primary_key and not column.foreign_keys
    }
    assert integers == from_api | from_files | counted, "classify the new Integer column's writers"


# ---------- a model's reply ----------


@pytest.mark.parametrize("column", sorted(MODEL), ids=lambda c: ".".join(c))
def test_a_reply_value_of_exactly_the_columns_length_is_valid_and_one_more_is_not(column):
    n = limited_columns()[column]
    MODEL[column]("x" * n)
    with pytest.raises(ValidationError, match=f"at most {n} characters"):
        MODEL[column]("x" * (n + 1))


def test_the_longest_chat_action_the_pattern_admits_fits_its_column():
    n = limited_columns()[("chat_messages", "suggested_action")]
    longest = "checkpoint:" + "9" * 12
    assert ChatReplyOut(reply="r", suggested_action=longest).suggested_action == longest and len(longest) <= n
    with pytest.raises(ValidationError):
        ChatReplyOut(reply="r", suggested_action=longest + "9")  # the pattern is what bounds it


# ---------- values the code defines ----------


@pytest.mark.parametrize("column", sorted(CLOSED), ids=lambda c: ".".join(c))
def test_the_longest_value_the_code_defines_fits_its_column(column):
    longest = max(CLOSED[column](), key=len)
    assert len(longest) <= limited_columns()[column], longest


def test_an_agent_name_the_registry_accepts_fits_every_column_that_holds_it():
    """AgentSpec's pattern allows 100 characters: tasks.owner, feedback.agent, model_calls.agent are all that long."""
    lengths = limited_columns()
    assert lengths[("agents", "name")] == lengths[("tasks", "owner")] == lengths[("feedback", "agent")]
    assert lengths[("model_calls", "agent")] >= lengths[("agents", "name")]


def _record_event_types():
    """Every event type in app/: the literal given to record_event (both arms of a conditional)."""
    found = []
    for path in Path(app_package.__file__).parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "record_event":
                arg = node.args[2] if len(node.args) > 2 else next((k.value for k in node.keywords if k.arg == "type"), None)
                arms = [arg.body, arg.orelse] if isinstance(arg, ast.IfExp) else [arg]
                found += [(path.name, node.lineno, a.value if isinstance(a, ast.Constant) else None) for a in arms]
    return found


def test_every_event_type_fits_its_column():
    types = _record_event_types()
    sources = [path.read_text(encoding="utf-8") for path in Path(app_package.__file__).parent.rglob("*.py")]
    # The scan finds every call a plain text search does (minus the function's own `def`).
    assert len({(f, line) for f, line, _ in types}) == sum(s.count("record_event(") for s in sources) - 1
    assert [(f, line) for f, line, t in types if not isinstance(t, str)] == [], "an event type must be a literal"
    assert [t for _, _, t in types if len(t) > limited_columns()[("run_events", "type")]] == []


def test_nothing_writes_the_unwritten_columns():
    """If one starts being written, classify its writers above (and bound them)."""
    for _table, column in NOT_WRITTEN:
        for path in Path(app_package.__file__).parent.rglob("*.py"):
            if path.name not in ("models.py", "schemas.py"):
                assert not re.search(rf"\b{column}\s*=[^=]", path.read_text(encoding="utf-8")), f"{path.name} writes {column}"


# ---------- files in registry/ ----------


def _skill_file(tmp_path, **meta):
    meta = {"name": "a-skill", "version": "1", "summary": "s", **meta}
    front = "\n".join(f"{k}: {v}" for k, v in meta.items())
    path = tmp_path / "a-skill.md"
    path.write_text(f"---\n{front}\n---\nbody\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("field", ["name", "summary"])
def test_a_skill_file_longer_than_its_column_is_refused_naming_the_file_and_the_field(tmp_path, field):
    n = limited_columns()[("skills", field)]
    assert parse_skill_file(_skill_file(tmp_path, **{field: "x" * n}))[field] == "x" * n
    with pytest.raises(RegistryError, match=rf"a-skill\.md: {field}: .*at most {n} characters"):
        parse_skill_file(_skill_file(tmp_path, **{field: "x" * (n + 1)}))


def test_a_skill_version_must_fit_32_bits(tmp_path):
    assert parse_skill_file(_skill_file(tmp_path, version=INT32_MAX))["version"] == INT32_MAX
    with pytest.raises(RegistryError, match="version"):
        parse_skill_file(_skill_file(tmp_path, version=INT32_MAX + 1))


def test_limits_come_from_the_columns_themselves():
    """One number per column: the helpers the validators use read it from the model, so they can't drift from it."""
    assert limit(Task, "title") == limited_columns()[("tasks", "title")]
    assert limit(Agent, "name") == limited_columns()[("agents", "name")]
    assert limit(Feedback, "rating") == limited_columns()[("feedback", "rating")]


@pytest.mark.parametrize("model, column", [(Skill, "body"), (Task, "input"), (Task, "priority"), (Task, "status")],
                         ids=["text", "json", "integer", "enum"])
def test_asking_for_the_length_of_a_column_without_one_is_a_clear_error(model, column):
    with pytest.raises(ValueError, match="no length limit"):
        limit(model, column)


# ---------- the cases that matter most, end to end ----------


@pytest.mark.parametrize("length", [201, 250, 300])
def test_a_completed_task_keeps_its_whole_title_in_memory_and_learning(client, session_factory, manual_project, length):
    """The proven case: manual_learning mode copies the title into learning_traces.concept right after the paid run.
    On PostgreSQL a 201-300 character title made that a 500 (the run paid for, the retry paid for again)."""
    pid = manual_project["id"]
    title = "T" * length
    t = client.post(f"/projects/{pid}/tasks", json={"title": title, "owner": "researcher"}).json()
    for status in ("READY", "RUNNING"):
        client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": status})
    use_fake_role(client, session_factory, "research", [RUN_RESULT])

    r = client.post(f"/projects/{pid}/tasks/{t['id']}/run")
    assert r.status_code == 200, r.text
    assert [lt["concept"] for lt in client.get(f"/projects/{pid}/learning").json()] == [title]
    assert [m["title"] for m in client.get(f"/projects/{pid}/memory").json()] == [title]


def test_a_model_id_of_the_longest_length_is_logged_with_the_call(client, project, session_factory):
    """Every call is logged with its model id after it was paid for; a longer id would lose that record (and the spend
    would go uncounted against the budget) on PostgreSQL, so the settings refuse one."""
    pid = project["id"]
    n = limited_columns()[("model_calls", "model")]
    use_provider(client, session_factory, FakeProvider(replies=[{"understanding": "u", "assumptions": [], "steps": []}]))
    assert client.put("/settings/global/0", json={"models": {"manager": {"provider": "fake", "model": "m" * n}}}).status_code == 200
    client.post(f"/projects/{pid}/plan")  # an empty plan is refused (422), but the call was made and logged
    calls = client.get(f"/projects/{pid}/model_calls").json()
    assert [c["model"] for c in calls] == ["m" * n]


def test_a_stored_model_id_too_long_for_the_call_log_is_refused_before_anything_is_paid(client, project, session, session_factory):
    """PUT /settings refuses such an id now, but a layer stored before it did can still hold one. The call would be
    paid for, then its log row (model_calls.model) refused by PostgreSQL: unrecorded, uncounted, a 500, and again on
    every retry. So the route is refused before anything is held or sent."""
    pid = project["id"]
    n = limited_columns()[("model_calls", "model")]
    fake = FakeProvider(replies=[{"understanding": "u", "assumptions": [], "steps": []}])

    def override():
        with session_factory() as s:
            yield Gateway(s, providers={"fake": fake})

    api.dependency_overrides[get_gateway] = override
    session.add(SettingsLayer(  # as stored before the limit: JSON, no length check
        scope=SettingsScope.GLOBAL, scope_id=0, values={"models": {"manager": {"provider": "fake", "model": "m" * (n + 1)}}}
    ))
    session.commit()

    r = client.post(f"/projects/{pid}/plan")
    assert r.status_code == 502 and f"longer than {n} characters" in r.json()["detail"], r.text
    assert fake.requests == []  # nothing was sent
    assert client.get(f"/projects/{pid}/model_calls").json() == []
    assert session.scalars(select(BudgetHold)).all() == []  # and nothing is held against the budget


# ---------- the convention that makes all of the above apply to a request body ----------


def _subclasses(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from _subclasses(sub)


# The request bodies that are not a StorableIn, and why nothing unstorable can reach a column through each.
PLAIN_BODIES = {
    "ChatIn": "chat.send_message refuses unstorable text itself, before the call (tests/test_chat.py pins its message)",
    "ProjectStageUpdate": "an enum",
    "ProjectPauseUpdate": "a boolean",
    "RatingIn": "an integer from 1 to 5",
    "BenchmarkRequest": "a list of RouteIn, which is a StorableIn",
}
UNTYPED_BODIES = {  # (method, path) of a body taken as a plain dict: who validates it
    ("POST", "/agents"): "AgentSpec, a StorableIn",
    ("PUT", "/settings/{scope}/{scope_id}"): "SettingsLayerIn, a StorableIn",
    ("POST", "/blueprints/validate"): "nothing is stored",
    ("POST", "/interview/answer"): "nothing is stored",
}


def test_every_request_body_is_checked_for_what_the_database_cannot_store():
    """The checks above hold for a request body because it derives StorableIn. A new endpoint whose body doesn't would
    pass every test and fail on PostgreSQL: it must derive it, or be listed here with the reason it needn't."""
    typed, untyped = set(), set()
    for path, operations in api.openapi()["paths"].items():
        for method, operation in operations.items():
            for media in operation.get("requestBody", {}).get("content", {}).values():
                ref = media["schema"].get("$ref")
                if ref:
                    typed.add(ref.rsplit("/", 1)[-1])
                else:
                    untyped.add((method.upper(), path))
    checked = {cls.__name__ for cls in _subclasses(StorableIn)}
    assert typed - checked == set(PLAIN_BODIES)
    assert untyped == set(UNTYPED_BODIES)
