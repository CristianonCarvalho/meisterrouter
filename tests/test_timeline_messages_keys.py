"""Every cli.timeline.web.* locale key must be exposed by the timeline page messages."""

import pytest

from meister.dashboard.server import _timeline_messages
from meister.i18n import reset_language_cache, set_language
from meister.locales import en_cli, pt_br_cli

PREFIX = "cli.timeline.web."
LOCALES = {"en": en_cli.MESSAGES, "pt-BR": pt_br_cli.MESSAGES}


def _timeline_keys(messages: dict) -> set:
    return {key[len(PREFIX):] for key in messages if key.startswith(PREFIX)}


@pytest.mark.parametrize("language", list(LOCALES))
def test_locale_timeline_keys_are_in_timeline_messages(language):
    locale_keys = _timeline_keys(LOCALES[language])
    assert locale_keys, f"no {PREFIX}* keys found in the {language} locale"
    set_language(language)
    try:
        exposed = set(_timeline_messages())
    finally:
        reset_language_cache()
    missing = sorted(key for key in locale_keys if key not in exposed)
    assert not missing, f"{language}: keys missing from _timeline_messages(): {missing}"


@pytest.mark.parametrize("language", list(LOCALES))
def test_timeline_messages_values_are_not_empty(language):
    set_language(language)
    try:
        messages = _timeline_messages()
    finally:
        reset_language_cache()
    empty = sorted(key for key, value in messages.items() if not str(value).strip())
    assert not empty, f"{language}: empty timeline messages: {empty}"
