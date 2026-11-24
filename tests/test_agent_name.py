"""An agent's name comes from its session title. A title with no English letters used to fall back to `agent-<session id>`
without a word; now the design asks for an English name before it can be approved (app/design.py::check)."""

from tests.test_factory_intake import approve, rid, run_through


def session_with_title(client, title):
    r = client.post("/factory/sessions?locale=en", json={"title": title, "request_id": rid()})
    assert r.status_code == 201, r.text
    return r.json()


def identity_issues(st):
    branch = next(b for b in st["design"]["branches"] if b["key"] == "identity")
    return branch["issues"]


def test_a_title_without_english_letters_asks_for_an_english_name(client):
    st = approve(client, run_through(client, session_with_title(client, "پشتیبانی تیکت"))).json()
    assert st["session"]["stage"] == "DESIGNING", st
    assert any(i.startswith("agent_core.name") for i in identity_issues(st)), identity_issues(st)


def test_an_english_title_gives_a_name_and_no_issue(client):
    st = approve(client, run_through(client, session_with_title(client, "Ticket triage"))).json()
    assert st["session"]["stage"] == "DESIGNING", st
    assert not any(i.startswith("agent_core.name") for i in identity_issues(st)), identity_issues(st)
