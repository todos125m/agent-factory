def test_create_and_list_feedback(client, project):
    pid = project["id"]
    task = client.post(f"/projects/{pid}/tasks", json={"title": "T", "owner": "researcher"}).json()

    r = client.post("/feedback", json={
        "project_id": pid, "task_id": task["id"], "agent": "researcher", "rating": "up", "note": "خوب بود",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["agent"] == "researcher" and body["rating"] == "up"

    r2 = client.post("/feedback", json={"project_id": pid, "agent": "researcher", "rating": "down"})
    assert r2.status_code == 201

    listing = client.get("/agents/researcher/feedback").json()
    assert len(listing) == 2
    assert {f["rating"] for f in listing} == {"up", "down"}


def test_feedback_rejects_unknown_project(client):
    r = client.post("/feedback", json={"project_id": 9999, "agent": "researcher", "rating": "up"})
    assert r.status_code == 404


def test_feedback_rejects_bad_rating(client, project):
    r = client.post("/feedback", json={"project_id": project["id"], "agent": "researcher", "rating": "meh"})
    assert r.status_code == 422
