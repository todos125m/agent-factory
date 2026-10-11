"""The owner's display preferences: four independent axes (docs/HUMAN_GUIDED_FACTORY.md §4).

- ui_locale (fa / en / tr): the language of the whole UI — questions, help, errors, notices.
- audience_level (ordinary / professional / developer): only how deep explanations go and which fields are shown.
- code_visibility (hidden / visible): whether code, schemas and raw JSON are shown; follows the level unless set.
- interaction_policy and quality_target are not here: they are settings layers (app/settings_layers.py), because
  they shape what the system does, per scope; these three only shape what the owner sees.

None of these grants anything: a developer level or visible code adds no permission, budget or approval. A value
the owner never chose stays None (not chosen) — the effective value is a proposal, never stored as their choice.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Project, UserPreference

LOCALES = ("fa", "en", "tr")
RTL_LOCALES = frozenset({"fa"})
# The product is Persian-first: until the owner picks, the UI speaks Persian (and asks on first entry).
DEFAULT_LOCALE = "fa"
# English is the translation fallback (web/i18n.js), the language a model is told by name.
LOCALE_NAMES = {"fa": "Persian (fa)", "en": "English (en)", "tr": "Turkish (tr)"}

AUDIENCE_LEVELS = ("ordinary", "professional", "developer")
DEFAULT_AUDIENCE = "ordinary"
CODE_VISIBILITY = ("hidden", "visible")
# Code is shown to developers and hidden from the others unless they ask for it.
DEFAULT_CODE_VISIBILITY = {"ordinary": "hidden", "professional": "hidden", "developer": "visible"}


def get(session: Session, user_id: int) -> UserPreference | None:
    return session.scalar(select(UserPreference).where(UserPreference.user_id == user_id))


def known(value: str | None, allowed: tuple[str, ...]) -> str | None:
    """A stored choice this version still offers, else None: an unknown one reads as not chosen, never as a failure."""
    return value if value in allowed else None


def chosen(pref: UserPreference | None) -> dict[str, str | None]:
    """The owner's own choices (None = not chosen yet)."""
    return {
        "ui_locale": known(pref.ui_locale, LOCALES) if pref else None,
        "audience_level": known(pref.audience_level, AUDIENCE_LEVELS) if pref else None,
        "code_visibility": known(pref.code_visibility, CODE_VISIBILITY) if pref else None,
    }


def effective(pref: UserPreference | None) -> dict[str, Any]:
    """What the UI uses now: the owner's choices, the defaults where they made none (or chose what this version no
    longer offers)."""
    mine = chosen(pref)
    locale = mine["ui_locale"] or DEFAULT_LOCALE
    level = mine["audience_level"] or DEFAULT_AUDIENCE
    code = mine["code_visibility"] or DEFAULT_CODE_VISIBILITY[level]
    return {
        "ui_locale": locale,
        "dir": "rtl" if locale in RTL_LOCALES else "ltr",
        "audience_level": level,
        "code_visibility": code,
    }


def reply_locale(session: Session, project: Project, output_locale: str) -> str | None:
    """The language a model should answer this project in, or None to leave it to the conversation.

    `output_locale` is the resolved setting (app/settings_layers.py): an explicit locale overrides; "auto" follows the
    language the owner chose for the UI. An owner who never chose one gets no instruction — the default Persian UI is
    a proposal, not their answer, and the model then follows the language they write in. The owner's own text is
    never translated: this only names the language of the model's reply."""
    if output_locale in LOCALES:
        return output_locale
    pref = get(session, project.owner_id)
    return pref.ui_locale if pref is not None and pref.ui_locale in LOCALES else None


def reply_language_line(session: Session, project: Project, output_locale: str) -> str | None:
    locale = reply_locale(session, project, output_locale)
    return f"Reply language: {LOCALE_NAMES[locale]}" if locale else None
