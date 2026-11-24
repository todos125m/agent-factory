"""One agent in full (GET /agents/{name}) carries what it was told and its locked output shape, for the Inspector tab; the
list every page loads does not."""

SCHEMA = {"type": "object", "properties": {"category": {"type": "string"}}, "required": ["category"]}
BASE = {"description": "d", "model_role": "research", "capabilities": ["web_research"], "tools": ["web_search"]}


def test_the_detail_has_the_instructions_and_the_output_schema_and_the_list_does_not(client):
    made = client.post("/agents", json={**BASE, "name": "inspected", "instructions": "Answer in JSON.",
                                        "output_schema": SCHEMA})
    assert made.status_code == 201, made.text
    detail = client.get("/agents/inspected").json()
    assert detail["instructions"] == "Answer in JSON." and detail["output_schema"] == SCHEMA
    row = next(a for a in client.get("/agents").json() if a["name"] == "inspected")
    assert "instructions" not in row and "output_schema" not in row


def test_an_agent_with_no_schema_says_so(client):
    client.post("/agents", json={**BASE, "name": "plain"})
    detail = client.get("/agents/plain").json()
    assert detail["output_schema"] is None and detail["instructions"] == ""
