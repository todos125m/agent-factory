from tests.test_blueprint import VALID_SPECIALIST


def test_validate_endpoint_accepts_valid_blueprint(client):
    resp = client.post("/blueprints/validate", json=VALID_SPECIALIST)
    assert resp.status_code == 200
    assert resp.json() == {"valid": True, "issues": []}


def test_validate_endpoint_reports_issues(client):
    resp = client.post("/blueprints/validate", json={"agent_core": VALID_SPECIALIST["agent_core"]})
    body = resp.json()
    assert body["valid"] is False
    assert body["issues"]
