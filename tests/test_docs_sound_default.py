"""A prosa sobre o som na documentação deve bater com o padrão do código (desligado)."""

from __future__ import annotations

import pathlib
import re

import pytest

from tests.timeline_js import run_js

ROOT = pathlib.Path(__file__).resolve().parent.parent
ADVANCED_EN = ROOT / "docs" / "advanced.md"
ADVANCED_PT = ROOT / "docs" / "pt-BR" / "INSTALACAO_E_COMANDOS_AVANCADOS.md"
CHANGELOG_EN = ROOT / "CHANGELOG.md"
CHANGELOG_PT = ROOT / "CHANGELOG.pt-BR.md"

# Frases que afirmam que o som abre ligado. A borda `\b` impede casar "desligado por padrão"
# (de outros recursos, como gate.docs_only) com "ligado por padrão".
SOUND_ON_BY_DEFAULT = (
    re.compile(r"\bon by default"),
    re.compile(r"\*\*on\*\* by default"),
    re.compile(r"\bligado por padrão"),
    re.compile(r"\*\*ligado\*\* por padrão"),
)


def test_parse_sound_preference_is_off_unless_stored_on() -> None:
    assert run_js("parseSoundPreference(null)") is False
    assert run_js("parseSoundPreference(undefined)") is False
    assert run_js('parseSoundPreference("on")') is True


def test_advanced_guide_says_sound_is_off_by_default() -> None:
    text = ADVANCED_EN.read_text(encoding="utf-8")
    assert re.search(r"\*\*Sound:\*\*[^\n]*?\*{0,2}off\*{0,2} by default", text), (
        f"{ADVANCED_EN.name} deve dizer que o som é off by default"
    )


def test_pt_guide_says_sound_is_off_by_default() -> None:
    text = ADVANCED_PT.read_text(encoding="utf-8")
    assert re.search(r"\*\*Som:\*\*[^\n]*?\*{0,2}desligado\*{0,2} por padrão", text), (
        f"{ADVANCED_PT.name} deve dizer que o som vem desligado por padrão"
    )


@pytest.mark.parametrize("path", [ADVANCED_EN, ADVANCED_PT, CHANGELOG_EN, CHANGELOG_PT], ids=lambda p: p.name)
def test_no_doc_claims_sound_is_on_by_default(path: pathlib.Path) -> None:
    text = path.read_text(encoding="utf-8").lower()
    for phrase in SOUND_ON_BY_DEFAULT:
        assert not phrase.search(text), f"{path.name} afirma que o som abre ligado: {phrase.pattern!r}"
