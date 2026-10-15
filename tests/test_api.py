def move(client, pid, tid, status, **extra):
    return client.post(f"/projects/{pid}/tasks/{tid}/transition", json={"status": status, **extra})


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_duplicate_email_rejected(client):
    assert client.post("/users", json={"email": "x@example.com"}).status_code == 201
    assert client.post("/users", json={"email": "x@example.com"}).status_code == 409


def test_project_defaults(project):
    assert project["mode"] == "automatic"
    assert project["stage"] == "IDEA"
    assert project["paused"] is False


def test_v1_task_graph_parallel_then_product(client, project):
    pid = project["id"]
    research = client.post(f"/projects/{pid}/tasks", json={"title": "Research", "owner": "researcher"}).json()
    customer = client.post(f"/projects/{pid}/tasks", json={"title": "Customer", "owner": "customer_researcher"}).json()
    product = client.post(
        f"/projects/{pid}/tasks",
        json={"title": "Product", "owner": "product", "depends_on": [research["id"], customer["id"]]},
    ).json()
    assert product["depends_on"] == [research["id"], customer["id"]]

    runnable = {t["id"] for t in client.get(f"/projects/{pid}/tasks/runnable").json()}
    assert runnable == {research["id"], customer["id"]}

    assert move(client, pid, product["id"], "READY").status_code == 409  # deps not done

    for t in (research, customer):
        for status in ("READY", "RUNNING", "COMPLETED"):
            assert move(client, pid, t["id"], status).status_code == 200

    assert [t["id"] for t in client.get(f"/projects/{pid}/tasks/runnable").json()] == [product["id"]]
    assert move(client, pid, product["id"], "READY").status_code == 200


def test_unknown_dependency_rejected(client, project):
    r = client.post(f"/projects/{project['id']}/tasks", json={"title": "X", "depends_on": [999]})
    assert r.status_code == 422


def test_invalid_transition_rejected(client, project):
    pid = project["id"]
    t = client.post(f"/projects/{pid}/tasks", json={"title": "T"}).json()
    assert move(client, pid, t["id"], "COMPLETED").status_code == 409


def test_retry_limit_then_escalate(client, project):
    pid = project["id"]
    t = client.post(f"/projects/{pid}/tasks", json={"title": "Flaky", "max_retries": 1}).json()
    for status in ("READY", "RUNNING", "FAILED", "READY", "RUNNING", "FAILED"):
        assert move(client, pid, t["id"], status).status_code == 200
    r = move(client, pid, t["id"], "READY")
    assert r.status_code == 409 and "Retry limit" in r.json()["detail"]
    assert move(client, pid, t["id"], "ESCALATED").json()["retries"] == 1


def test_review_loop_and_output(client, project):
    pid = project["id"]
    t = client.post(f"/projects/{pid}/tasks", json={"title": "Build"}).json()
    for status in ("READY", "RUNNING"):
        move(client, pid, t["id"], status)
    done = move(client, pid, t["id"], "COMPLETED", output={"pr": 1}).json()
    assert done["output"] == {"pr": 1}
    assert move(client, pid, t["id"], "READY", reason="CHANGES_REQUIRED").status_code == 200


def test_every_transition_is_logged(client, project):
    pid = project["id"]
    t = client.post(f"/projects/{pid}/tasks", json={"title": "T"}).json()
    move(client, pid, t["id"], "READY", reason="plan approved")
    events = client.get(f"/projects/{pid}/events").json()
    assert [e["type"] for e in events] == ["project.created", "task.created", "task.status_changed"]
    assert events[-1]["payload"] == {"from": "CREATED", "to": "READY", "reason": "plan approved"}


def test_project_stage_and_pause(client, project):
    pid = project["id"]
    assert client.post(f"/projects/{pid}/stage", json={"stage": "BUILDING"}).status_code == 409
    assert client.post(f"/projects/{pid}/stage", json={"stage": "DISCOVERY"}).json()["stage"] == "DISCOVERY"

    client.post(f"/projects/{pid}/pause", json={"paused": True})
    t = client.post(f"/projects/{pid}/tasks", json={"title": "T"}).json()
    assert move(client, pid, t["id"], "READY").status_code == 409
    assert client.post(f"/projects/{pid}/stage", json={"stage": "VALIDATION"}).status_code == 409
