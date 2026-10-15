"""Night queue item 7: source quality, not just source presence — a deterministic tier per cited URL
(app/sources.py, rules in registry/source_tiers.yaml) and the FACT guard downgrading a FACT whose only
sources are lowest tier. FakeProvider only, no real model calls.
"""

import pytest
from pydantic import ValidationError

from app import sources
from app.main import app
from app.routers.manager import get_gateway
from tests.test_task_run import use_fake_role
from tests.test_web_search import BASE_RESULT, _start_researcher_task

# ---------- tiers (pure code) ----------


@pytest.mark.parametrize("url,tier", [
    # 1: official, academic, statistics
    ("https://www.census.gov/data/tables.html", 1),
    ("https://ocw.mit.edu/courses", 1),
    ("https://www.who.int/news/item/x", 1),
    ("https://www.ons.gov.uk/economy", 1),
    ("https://www.gov.uk/guidance/x", 1),  # the portal itself: two labels under a country code
    ("https://www.canada.ca/en/x", 1),
    ("https://ut.ac.ir/fa/news", 1),
    ("https://www.amar.org.ir/news", 1),
    ("https://data.worldbank.org/indicator/NY.GDP", 1),
    ("https://arxiv.org/abs/2401.00001", 1),
    # 2: app stores, reviews, market data, publishers
    ("https://apps.apple.com/us/app/x/id123", 2),
    ("https://play.google.com/store/apps/details?id=x", 2),
    ("https://cafebazaar.ir/app/x", 2),
    ("https://www.g2.com/products/x/reviews", 2),
    ("https://www.statista.com/statistics/1/", 2),
    ("https://www.reuters.com/technology/x", 2),
    # 3: blogs, forums, social, unknown
    ("https://medium.com/@someone/post", 3),
    ("https://someone.substack.com/p/x", 3),
    ("https://www.reddit.com/r/freelance/comments/x", 3),
    ("https://virgool.io/@x/post", 3),
    ("https://restaurant-insider.example/2025/diner-survey", 3),
    ("https://blog.mit.edu/post", 3),  # a blog subdomain stays a blog, even under .edu
    ("https://en.wikipedia.org/wiki/Invoice", 3),
    # adversarial: lookalikes must not inherit a trusted tier
    ("https://who.int.evil.example/report", 3),
    ("https://www.census.gov@evil.example/report", 3),
    ("https://evil.example/?ref=https://www.census.gov", 3),
    ("https://go.com/x", 3),
    ("https://go.to/diner-survey", 3),  # "go." is a registrable name there (a redirect service), not a government
    ("https://edu.ac/x", 3),
    ("https://gov/stats", 3),  # a bare single-label host is not a publisher
    ("https://www.stats.go.jp/data", 1),
    ("http://192.168.1.10/report", 3),
    ("http://localhost:8000/x", 3),
    ("https:///no-host", 3),
])
def test_host_tier(url, tier):
    assert sources.host_tier(sources.url_host(url)) == tier


def test_cited_urls_stop_at_prose_punctuation():
    text = ("طبق گزارش https://www.amar.org.ir/news، و [WHO](https://www.who.int/x). "
            "Also (https://www.census.gov/data), HTTPS://APPS.APPLE.COM/us/app/x; "
            "https://a.example,https://b.example")
    hosts = [sources.url_host(u) for u in sources.cited_urls(text)]
    assert hosts == ["amar.org.ir", "who.int", "census.gov", "apps.apple.com", "a.example", "b.example"]


def test_a_url_inside_a_query_string_is_not_a_second_source():
    assert sources.cited_urls("see https://evil.example/?ref=https://www.census.gov") == [
        "https://evil.example/?ref=https://www.census.gov",
    ]


def test_registry_file_is_valid_and_unknown_sites_are_lowest():
    assert sources.load_rules().default_tier == sources.LOWEST_TIER


def test_an_internationalized_domain_matches_its_punycode_entry():
    persian_host = "مرکزآمار.ir"  # «مرکزآمار.ir»
    rules = sources.SourceRules.model_validate({
        "default_tier": 3, "blog_labels": [], "official_tlds": [], "tiers": {1: ["xn--hgbk2acc6id11f.ir"]},
    })
    host = sources.url_host(f"https://www.{persian_host}/report")
    assert host == "xn--hgbk2acc6id11f.ir"
    assert sources.host_tier(host, rules) == 1


@pytest.mark.parametrize("tiers,error", [
    ({1: ["example.com"], 2: ["example.com"]}, "listed in tiers"),
    ({4: ["example.com"]}, "between 1 and 3"),
    ({1: ["https://example.com"]}, "bare lowercase domain"),
    ({1: ["www.example.com"]}, "bare lowercase domain"),
])
def test_invalid_rules_are_rejected(tiers, error):
    base = {"default_tier": 3, "blog_labels": [], "official_tlds": []}
    with pytest.raises(ValidationError, match=error):
        sources.SourceRules.model_validate({**base, "tiers": tiers})


# ---------- FACT guard, end to end through /run ----------


def _run_with_findings(client, session_factory, project, findings):
    pid, tid = _start_researcher_task(client, session_factory, project)
    use_fake_role(client, session_factory, "research", [{**BASE_RESULT, "findings": findings}])
    r = client.post(f"/projects/{pid}/tasks/{tid}/run")
    assert r.status_code == 200, r.text
    events = [e for e in client.get(f"/projects/{pid}/events").json() if e["type"] == "evidence.downgraded"]
    app.dependency_overrides.pop(get_gateway, None)
    return r.json()["task"]["output"]["findings"], events


def test_fact_backed_only_by_a_blog_survey_is_downgraded(client, session_factory, project):
    """The real-run case: a restaurant blog's survey labeled FACT."""
    claim = "68% of diners prefer online ordering"
    findings, events = _run_with_findings(client, session_factory, project, [{
        "claim": claim, "type": "FACT",
        "basis": "survey at https://www.restaurant-insider.example/blog/2025-diner-survey, https://medium.com/@x/y",
    }])
    assert findings[0]["type"] == "INFERENCE"
    assert findings[0]["basis"].endswith(
        "[downgraded from FACT: only low-tier sources (blog/forum/unknown): restaurant-insider.example, medium.com]"
    )
    assert events[0]["payload"]["claims"] == [claim]
    assert events[0]["payload"]["details"] == [
        {"claim": claim, "reason": "low_tier_sources", "hosts": ["restaurant-insider.example", "medium.com"]},
    ]


@pytest.mark.parametrize("basis", [
    "https://www.census.gov/data/x",  # tier 1 only
    "https://apps.apple.com/us/app/x/id1",  # tier 2 only
    "https://medium.com/@x/y and https://www.oecd.org/x",  # a blog plus one tier-1 source
])
def test_fact_with_a_better_than_lowest_source_stays_a_fact(client, session_factory, project, basis):
    findings, events = _run_with_findings(client, session_factory, project, [
        {"claim": "c", "type": "FACT", "basis": basis},
    ])
    assert findings[0] == {"claim": "c", "type": "FACT", "basis": basis}
    assert events == []


def test_no_url_downgrade_keeps_its_reason(client, session_factory, project):
    findings, events = _run_with_findings(client, session_factory, project, [
        {"claim": "c", "type": "FACT", "basis": "recalled knowledge"},
    ])
    assert findings[0]["basis"] == "recalled knowledge [downgraded from FACT: no source URL cited]"
    assert events[0]["payload"]["details"] == [{"claim": "c", "reason": "no_url", "hosts": []}]


def test_low_tier_inference_is_left_alone(client, session_factory, project):
    findings, events = _run_with_findings(client, session_factory, project, [
        {"claim": "c", "type": "INFERENCE", "basis": "https://medium.com/@x/y"},
    ])
    assert findings[0] == {"claim": "c", "type": "INFERENCE", "basis": "https://medium.com/@x/y"}
    assert events == []
