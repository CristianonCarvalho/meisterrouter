"""Paridade dos catálogos de mensagens: toda chave existe em en e em pt-BR, com os mesmos campos."""

import re
import string

from meister.i18n import CATALOGS, DUPLICATE_KEYS, t

_ACCENTED = re.compile(r"[áéíóúâêôãõçÁÉÍÓÚÂÊÔÃÕÇ]")


def _fields(message: str) -> set:
    return {name for _, name, _, _ in string.Formatter().parse(message) if name}


def test_catalogs_have_the_same_keys_and_fields():
    en, pt = CATALOGS["en"], CATALOGS["pt-BR"]
    assert set(en) == set(pt), f"só em en: {sorted(set(en) - set(pt))} | só em pt-BR: {sorted(set(pt) - set(en))}"
    for key in en:
        assert _fields(en[key]) == _fields(pt[key]), f"{key}: campos diferentes entre os idiomas"


def test_no_duplicate_keys_across_catalog_modules():
    assert DUPLICATE_KEYS == []


def test_messages_are_non_empty_and_english_has_no_portuguese_accents():
    for key, message in CATALOGS["en"].items():
        assert message.strip(), f"{key}: mensagem vazia em en"
        assert not _ACCENTED.search(message), f"{key}: texto em inglês com letra acentuada do português"
    for key, message in CATALOGS["pt-BR"].items():
        assert message.strip(), f"{key}: mensagem vazia em pt-BR"


def test_every_key_formats_in_both_languages(monkeypatch):
    from meister.i18n import reset_language_cache, set_language

    for language in ("en", "pt-BR"):
        set_language(language)
        for key, message in CATALOGS[language].items():
            rendered = t(key, **{name: "x" for name in _fields(message)})
            assert rendered and "{" not in rendered, f"{language}:{key}: {rendered!r}"
    reset_language_cache()
