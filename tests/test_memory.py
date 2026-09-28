"""Phase 7 — Memory (docs/ARCHITECTURE.md §24-26): Project Memory captured from a completed task,
a Learning Trace captured only in manual_learning mode, and retrieval into task_context. FakeProvider
only, no real model calls.
"""

from app.main import app
from app.routers.manager import get_gateway
from tests.test_task_run import RUN_RESULT, use_fake_role


def _running_task(client, pid, owner, **kw):
    t = client.post(f"/projects/{pid}/tasks", json={"title": "T", "owner": owner, **kw}).json()
    client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": "READY"})
    client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": "RUNNING"})
    return t


def test_run_task_captures_project_memory_with_agent_category(client, session_factory, project):
    pid = project["id"]
    t = _running_task(client, pid, "researcher")
    use_fake_role(client, session_factory, "research", [RUN_RESULT])
    r = client.post(f"/projects/{pid}/tasks/{t['id']}/run")
    assert r.status_code == 200, r.text

    items = client.get(f"/projects/{pid}/memory").json()
    assert len(items) == 1
    assert items[0]["category"] == "research"  # researcher -> RESEARCH
    assert items[0]["title"] == "T"
    assert items[0]["content"] == RUN_RESULT["summary"]
    assert items[0]["task_id"] == t["id"]

    events = [e["type"] for e in client.get(f"/projects/{pid}/events").json()]
    assert "memory.captured" in events
    app.dependency_overrides.pop(get_gateway, None)


def test_unmapped_agent_falls_back_to_technical_category(client, session_factory, project):
    pid = project["id"]
    t = _running_task(client, pid, "customer")
    use_fake_role(client, session_factory, "research", [RUN_RESULT])
    client.post(f"/projects/{pid}/tasks/{t['id']}/run")
    assert client.get(f"/projects/{pid}/memory").json()[0]["category"] == "customer"
    app.dependency_overrides.pop(get_gateway, None)


def test_manual_learning_mode_captures_a_learning_trace(client, session_factory, project):
    pid = project["id"]
    client.put(f"/settings/project/{pid}", json={"mode": "manual_learning"})
    t = _running_task(client, pid, "product")
    use_fake_role(client, session_factory, "research", [RUN_RESULT])
    client.post(f"/projects/{pid}/tasks/{t['id']}/run")

    traces = client.get(f"/projects/{pid}/learning").json()
    assert len(traces) == 1
    assert traces[0]["concept"] == "T"
    assert traces[0]["explanation"] == RUN_RESULT["lesson"]
    assert traces[0]["task_id"] == t["id"]

    events = [e["type"] for e in client.get(f"/projects/{pid}/events").json()]
    assert "learning.captured" in events
    app.dependency_overrides.pop(get_gateway, None)


def test_automatic_mode_does_not_capture_a_learning_trace(client, session_factory, project):
    pid = project["id"]
    client.put(f"/settings/project/{pid}", json={"mode": "automatic"})
    t = _running_task(client, pid, "strategy")
    use_fake_role(client, session_factory, "research", [RUN_RESULT])
    client.post(f"/projects/{pid}/tasks/{t['id']}/run")

    assert client.get(f"/projects/{pid}/learning").json() == []
    assert len(client.get(f"/projects/{pid}/memory").json()) == 1  # project memory is unconditional
    app.dependency_overrides.pop(get_gateway, None)


def test_memory_category_filter(client, session_factory, project):
    pid = project["id"]
    t1 = _running_task(client, pid, "researcher")
    use_fake_role(client, session_factory, "research", [RUN_RESULT])
    client.post(f"/projects/{pid}/tasks/{t1['id']}/run")

    t2 = _running_task(client, pid, "product")
    use_fake_role(client, session_factory, "research", [RUN_RESULT])
    client.post(f"/projects/{pid}/tasks/{t2['id']}/run")

    research_only = client.get(f"/projects/{pid}/memory", params={"category": "research"}).json()
    assert len(research_only) == 1 and research_only[0]["category"] == "research"
    app.dependency_overrides.pop(get_gateway, None)


def test_manual_memory_create_and_reject_task_from_other_project(client, project):
    pid = project["id"]
    r = client.post(f"/projects/{pid}/memory", json={"category": "brief", "title": "Initial brief", "content": "..."})
    assert r.status_code == 201, r.text
    assert r.json()["task_id"] is None

    other_user = client.post("/users", json={"email": "other@example.com"}).json()
    other_project = client.post("/projects", json={"owner_id": other_user["id"], "title": "O", "goal": "g"}).json()
    other_task = client.post(f"/projects/{other_project['id']}/tasks", json={"title": "OT"}).json()
    bad = client.post(f"/projects/{pid}/memory", json={
        "category": "brief", "title": "x", "content": "y", "task_id": other_task["id"],
    })
    assert bad.status_code == 404


def test_redo_loop_still_sees_the_tasks_own_prior_memory(client, session_factory, project):
    """COMPLETED -> READY -> RUNNING (the reviewer's revise loop) is a real transition
    (app/state_machine.py). On the second run, the task's own earlier finding must still be
    retrievable — it must not be excluded just because it was captured by this same task."""
    from app.context import task_context
    from app.models import Task

    pid = project["id"]
    t = _running_task(client, pid, "researcher")
    use_fake_role(client, session_factory, "research", [RUN_RESULT])
    client.post(f"/projects/{pid}/tasks/{t['id']}/run")
    assert client.get(f"/projects/{pid}/tasks/{t['id']}").json()["status"] == "COMPLETED"

    client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": "READY"})
    client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": "RUNNING"})

    with session_factory() as s:
        task = s.get(Task, t["id"])
        ctx = task_context(s, task)
    assert RUN_RESULT["summary"] in ctx
    app.dependency_overrides.pop(get_gateway, None)


def test_memory_title_over_column_length_is_rejected(client, project):
    pid = project["id"]
    r = client.post(f"/projects/{pid}/memory", json={"category": "brief", "title": "x" * 301, "content": "y"})
    assert r.status_code == 422


def test_memory_section_does_not_starve_dependency_results_under_tight_budget(client, session_factory, project):
    """A fixed per-item cap on the memory section could alone exhaust a small max_chars budget and
    make the truncate-and-break loop drop the dependency section entirely."""
    from app.context import task_context
    from app.models import Task

    pid = project["id"]
    for i in range(5):
        client.post(f"/projects/{pid}/memory", json={
            "category": "research", "title": f"Finding {i}", "content": "x" * 400,
        })
    dep = client.post(f"/projects/{pid}/tasks", json={"title": "Dep"}).json()
    with session_factory() as s:
        dep_task = s.get(Task, dep["id"])
        dep_task.output = {"summary": "CRITICAL DEPENDENCY OUTPUT THE NEXT TASK NEEDS"}
        s.commit()

    next_task = client.post(f"/projects/{pid}/tasks", json={"title": "Next", "depends_on": [dep["id"]]}).json()
    client.put(f"/settings/task/{next_task['id']}", json={"context": {"max_chars": 1500}})

    with session_factory() as s:
        task = s.get(Task, next_task["id"])
        ctx = task_context(s, task)
    assert "CRITICAL DEPENDENCY OUTPUT THE NEXT TASK NEEDS" in ctx


def test_task_context_includes_relevant_memory_including_manually_added(client, session_factory, project):
    """Manually-added memory (task_id: null) must not be silently excluded from retrieval — a plain
    `MemoryItem.task_id != task.id` SQL comparison would drop every NULL row."""
    from app.context import task_context
    from app.models import Task

    pid = project["id"]
    client.post(f"/projects/{pid}/memory", json={"category": "brief", "title": "Brief", "content": "Solo consultants"})
    t1 = _running_task(client, pid, "researcher")
    use_fake_role(client, session_factory, "research", [RUN_RESULT])
    client.post(f"/projects/{pid}/tasks/{t1['id']}/run")

    with session_factory() as s:
        t2 = client.post(f"/projects/{pid}/tasks", json={"title": "Next"}).json()
        task = s.get(Task, t2["id"])
        ctx = task_context(s, task)
    assert "Relevant project memory" in ctx
    assert "Brief" in ctx and "Solo consultants" in ctx  # manually-added (task_id: null) survives
    assert RUN_RESULT["summary"] in ctx
    app.dependency_overrides.pop(get_gateway, None)
