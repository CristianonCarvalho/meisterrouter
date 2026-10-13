"""A opção scope.allow_dependency_files precisa estar documentada nos dois idiomas, no exemplo e nos changelogs."""

from __future__ import annotations

import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
ADVANCED_EN = ROOT / "docs" / "advanced.md"
ADVANCED_PT = ROOT / "docs" / "pt-BR" / "INSTALACAO_E_COMANDOS_AVANCADOS.md"
CHANGELOG_EN = ROOT / "CHANGELOG.md"
CHANGELOG_PT = ROOT / "CHANGELOG.pt-BR.md"
EXAMPLE = ROOT / "meister.config.example.yaml"


def _since_0_9_1(path: pathlib.Path) -> str:
    """Tudo o que o changelog registra depois da 0.9.1 (a seção [Unreleased] e as versões lançadas desde então)."""
    text = path.read_text(encoding="utf-8")
    return text.split("## [Unreleased]", 1)[1].split("\n## [0.9.1]", 1)[0]


@pytest.mark.parametrize("path", [ADVANCED_EN, ADVANCED_PT], ids=lambda p: p.name)
def test_advanced_guide_documents_option_and_its_limits(path: pathlib.Path) -> None:
    line = next((ln for ln in path.read_text(encoding="utf-8").splitlines() if "scope.allow_dependency_files" in ln), "")
    assert line, f"{path.name} não documenta scope.allow_dependency_files"
    assert "`false`" in line
    assert "meister.config.yaml" in line


@pytest.mark.parametrize("path", [CHANGELOG_EN, CHANGELOG_PT], ids=lambda p: p.name)
def test_changelog_unreleased_mentions_option(path: pathlib.Path) -> None:
    assert "scope.allow_dependency_files" in _since_0_9_1(path)


def test_example_config_shows_option_commented_with_default_false() -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    assert re.search(r"^\s*#\s*allow_dependency_files: false\s*$", text, re.M)
    assert "allow_dependency_files" not in (yaml.safe_load(text).get("scope") or {})
