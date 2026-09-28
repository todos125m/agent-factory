"""Source quality for the deterministic FACT guard (app/manager.py::_apply_evidence_guard): the tier of a
cited URL comes from domain rules in `registry/source_tiers.yaml` — pure code, no model calls, no network.
Tier 1 = official/academic/statistics bodies, 2 = app stores, review platforms, company pages and
established publishers, 3 = blogs, forums, social and unknown sites (the lowest tier).
"""

import ipaddress
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

SOURCE_TIERS_FILE = Path(__file__).resolve().parent.parent / "registry" / "source_tiers.yaml"
LOWEST_TIER = 3

# Stops at whitespace, quotes, brackets and list punctuation (incl. Persian "،" "؛") so a link written
# inside prose, a Markdown link or a comma-separated list yields its real host.
URL_RE = re.compile(r"https?://[^\s<>\"'()\[\]{},;،؛]+", re.IGNORECASE)
_TRAILING = ".:!?…»”’"
_DOMAIN_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")


class SourceRules(BaseModel):
    default_tier: int = Field(ge=1, le=LOWEST_TIER)
    blog_labels: list[str]
    official_tlds: list[str]
    tiers: dict[int, list[str]]
    _domain_tier: dict[str, int] = PrivateAttr(default_factory=dict)

    @field_validator("blog_labels", "official_tlds")
    @classmethod
    def _lower(cls, labels: list[str]) -> list[str]:
        return [label.lower().strip(".") for label in labels]

    @field_validator("tiers")
    @classmethod
    def _valid_tiers(cls, tiers: dict[int, list[str]]) -> dict[int, list[str]]:
        seen: dict[str, int] = {}
        for tier, domains in tiers.items():
            if not 1 <= tier <= LOWEST_TIER:
                raise ValueError(f"tier {tier} must be between 1 and {LOWEST_TIER}")
            for domain in domains:
                if not _DOMAIN_RE.match(domain) or domain.startswith("www."):
                    raise ValueError(f"'{domain}' must be a bare lowercase domain (no scheme, path or www.)")
                if domain in seen:
                    raise ValueError(f"'{domain}' is listed in tiers {seen[domain]} and {tier}")
                seen[domain] = tier
        return tiers

    @model_validator(mode="after")
    def _index(self) -> "SourceRules":
        self._domain_tier = {d: tier for tier, domains in self.tiers.items() for d in domains}
        return self

    def tier_of(self, domain: str) -> int | None:
        return self._domain_tier.get(domain)


@lru_cache(maxsize=4)
def load_rules(path: Path = SOURCE_TIERS_FILE) -> SourceRules:
    return SourceRules.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def cited_urls(text: str) -> list[str]:
    return [m.rstrip(_TRAILING) for m in URL_RE.findall(text)]


def url_host(url: str) -> str | None:
    """Lowercased ASCII (punycode) host without a leading "www." or a trailing dot; None when there is none."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    host = (host or "").rstrip(".")
    try:
        host = host.encode("idna").decode("ascii")  # an internationalized domain matches its punycode entry
    except UnicodeError:
        pass  # not a valid IDN: kept as-is, so it matches no rule and gets the default tier
    host = host[4:] if host.startswith("www.") else host
    return host or None


def host_tier(host: str | None, rules: SourceRules | None = None) -> int:
    rules = rules or load_rules()
    if not host:
        return rules.default_tier
    try:
        ipaddress.ip_address(host)
        return rules.default_tier  # a bare IP is never an identifiable publisher
    except ValueError:
        pass
    if (exact := rules.tier_of(host)) is not None:
        return exact
    labels = host.split(".")
    if labels[0] in rules.blog_labels:
        return LOWEST_TIER
    for i in range(1, len(labels) - 1):  # longest listed parent domain first
        if (parent := rules.tier_of(".".join(labels[i:]))) is not None:
            return parent
    if len(labels) >= 2 and labels[-1] in rules.official_tlds:  # a bare "gov" host is not a publisher
        return 1
    return rules.default_tier
