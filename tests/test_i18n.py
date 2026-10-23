"""The UI's three languages (web/i18n.js, docs/FACTORY_UI_SPEC.md): app.js holds keys, never text; every key it uses
exists in fa, en and tr with the same placeholders; every value it labels with lbl(group, value) has a label."""

import json
import re
from pathlib import Path

import pytest

from app import build, credentials, external, runtime, workflow
from app.design import ALLOWED_SOURCES, BRANCHES, EDITABLE
from app.factory import field_specs
from app.registry import KNOWN_TOOLS
from app.models import FactoryStage, MemoryCategory, ProjectStage, RiskLevel, TaskStatus
from app.preferences import AUDIENCE_LEVELS, CODE_VISIBILITY, LOCALES
from app.registry import MODEL_ROLES
from app.settings_layers import INTERACTION_POLICIES, OUTPUT_LOCALES, QUALITY_TARGETS

WEB = Path(__file__).resolve().parent.parent / "web"
PERSIAN = re.compile(r"[؀-ۿ]")


def _messages() -> dict[str, dict[str, str]]:
    text = (WEB / "i18n.js").read_text(encoding="utf-8")
    return json.loads(re.search(r"/\*MESSAGES\*/(.*?)/\*END MESSAGES\*/", text, re.S).group(1))


def _app() -> str:
    return (WEB / "app.js").read_text(encoding="utf-8")


def _string_literals(src: str) -> list[str]:
    """Every '...' / "..." literal of a JS source, comments skipped."""
    out, i, n = [], 0, len(src)
    while i < n:
        if src.startswith("//", i):
            i = src.find("\n", i) if src.find("\n", i) >= 0 else n
        elif src.startswith("/*", i):
            i = src.index("*/", i) + 2
        elif src[i] in "'\"":
            quote, j = src[i], i + 1
            while src[j] != quote:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i + 1:j])
            i = j + 1
        else:
            i += 1
    return out


def test_the_three_languages_have_the_same_keys_and_placeholders():
    messages = _messages()
    assert set(messages) == set(LOCALES)
    keys = set(messages["en"])
    for locale in LOCALES:
        assert set(messages[locale]) == keys, (locale, set(messages[locale]) ^ keys)
        for key, text in messages[locale].items():
            assert text.strip(), (locale, key)
            assert set(re.findall(r"\{(\w+)\}", text)) == set(re.findall(r"\{(\w+)\}", messages["en"][key])), (locale, key)


def test_only_the_persian_dictionary_is_in_persian():
    messages = _messages()
    for locale in ("en", "tr"):
        assert not [k for k, v in messages[locale].items() if PERSIAN.search(v)], locale


def test_app_js_holds_no_text_of_its_own():
    assert not [s for s in _string_literals(_app()) if PERSIAN.search(s)]


def test_every_key_app_js_uses_exists():
    messages = _messages()["en"]
    # A whole literal argument (t('a.b') or t('a.b', {...})); a built key (t('err.' + code)) is checked by its group.
    used = set(re.findall(r"\bt\('([\w.]*\w)'\s*[,)]", _app())) | set(re.findall(r"tIn\(\w+, '([\w.]+)'\)", _app()))
    html = (WEB / "index.html").read_text(encoding="utf-8")
    used |= set(re.findall(r'data-i18n="([\w.]+)"', html))
    assert used, "no keys found: did app.js stop using t()?"
    assert used - set(messages) == set()


LABELLED = {
    "stage": [s.value for s in ProjectStage],
    "status": [s.value for s in TaskStatus],
    "risk": [r.value for r in RiskLevel],
    "mode": ["automatic", "manual_learning"],
    "memcat": [c.value for c in MemoryCategory],
    "role": sorted(MODEL_ROLES),
    "approval": ["auto", "manager", "user"],
    "access": ["api_key", "claude_account", "ollama"],
    "effort": ["low", "medium", "high"],
    "depth": ["quick", "standard", "deep"],
    "evidence": ["FACT", "INFERENCE", "HYPOTHESIS"],
    "inbox.kind": ["plan", "checkpoint", "ready", "blocked", "waiting"],
    "inbox.head": ["plan", "checkpoint", "ready", "blocked", "waiting"],
    "treason": [*sorted(runtime.STOP_REASONS), *sorted(runtime.WAITING_REASONS.values())],
    "rstate": sorted(runtime.RUN_STATES),
    "rstep": sorted(runtime.STEP_KINDS),
    "raction": ["finish", "tool", "ask_user"],
    "rrefused": sorted(runtime.REFUSALS),
    "tstate": sorted(runtime.EXECUTION_STATES),
    "vstate": sorted(build.VERSION_STATES),
    "ctest": list(credentials.TEST_RESULTS),
    "apscope": list(external.SCOPES),
    "cprovider": list(credentials.PROVIDERS),
    "wfkind": list(workflow.EDGE_KINDS),
    "wfnode": list(workflow.NODE_TYPES),
    "wfport": sorted({p for _, o, i in workflow.PORTS.values() for p in (o, i)}),
    "wffield": [*workflow.OUTPUT_FIELDS, *workflow.INPUT_FIELDS],
    "wfissue": sorted(workflow.ISSUE_CODES),
    "verdict": sorted(build.VERDICTS),
    "policy": list(INTERACTION_POLICIES),
    "quality": list(QUALITY_TARGETS),
    "outloc": list(OUTPUT_LOCALES),
    "level": list(AUDIENCE_LEVELS),
    "level_help": list(AUDIENCE_LEVELS),
    "code": list(CODE_VISIBILITY),
    "resource": ["project", "task", "user", "workspace", "agent", "skill", "benchmark_run", "global", "item",
                 "factory_session", "workflow", "credential", "api_key"],
    "fstatus": ["draft", "proposed", "confirmed"],
    "fsource": ["user", "default", "suggestion", "unknown"],
    "fstage": [s.value for s in FactoryStage],
    "bfield": [s["field"] for s in field_specs()],
    "build.step": ["understand", "brief", "design", "build", "evaluate", "active"],
    "build.conflict": ["internet_vs_constraints"],
    "branch": list(BRANCHES),
    "branch_help": list(BRANCHES),
    "dstatus": ["proposed", "confirmed", "incomplete"],
    "dsource": ["system", "user", "model"],
    "dtest": ["normal", "edge", "failure"],
    "dfield": sorted({p for paths in EDITABLE.values() for p in paths}
                     | {"data_storage.backend", "scalability.deployment", "memory.short_term", "observation.record",
                        "test_plan"}),
    "dchoice": ["always", "checkpoint", "auto", "public", "internal", "sensitive", *ALLOWED_SOURCES, *sorted(KNOWN_TOOLS)],
    "theme": ["light", "dark"],
}


@pytest.mark.parametrize("group", sorted(LABELLED))
def test_every_value_shown_with_a_label_has_one(group):
    messages = _messages()["en"]
    assert [v for v in LABELLED[group] if f"{group}.{v}" not in messages] == []


def test_every_label_group_app_js_uses_is_checked():
    groups = set(re.findall(r"lbl\('([\w.]+)'", _app())) | {"inbox.head", "level_help", "build.step", "build.conflict", "branch_help"}
    groups |= {g for g in re.findall(r"selectField\([^)]*?, '(\w+)'\)", _app())}
    assert groups - set(LABELLED) == set()


def test_the_language_names_are_their_own():
    text = (WEB / "i18n.js").read_text(encoding="utf-8")
    assert "fa: 'فارسی'" in text and "en: 'English'" in text and "tr: 'Türkçe'" in text


# Keys app.js builds at run time: t('err.' + code), lbl(group, value) and the like.
BUILT_PREFIXES = tuple(f"{group}." for group in LABELLED) + ("err.",)


def test_every_defined_key_is_used():
    """A key nothing shows is text translators keep up for no one."""
    used = set(re.findall(r"\bt\('([\w.]*\w)'\s*[,)]", _app())) | set(re.findall(r"tIn\(\w+, '([\w.]+)'\)", _app()))
    used |= set(re.findall(r'data-i18n="([\w.]+)"', (WEB / "index.html").read_text(encoding="utf-8")))
    unused = [k for k in _messages()["en"] if k not in used and not k.startswith(BUILT_PREFIXES)]
    assert unused == []
