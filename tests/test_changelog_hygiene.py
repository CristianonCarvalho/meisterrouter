"""Higiene do CHANGELOG em inglês e em português, na seção `## [Unreleased]`.

Os testes são herméticos: só leem `CHANGELOG.md` e `CHANGELOG.pt-BR.md`.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
EN_PATH = ROOT / "CHANGELOG.md"
PT_PATH = ROOT / "CHANGELOG.pt-BR.md"

# Títulos de seção em inglês e seus equivalentes em português (na mesma ordem usada nos arquivos).
SECTION_MAP = {
    "Changed (incompatible)": "Changed (incompatible)",
    "Added": "Adicionado",
    "Fixed": "Corrigido",
    "Other changes": "Outras alterações",
}

# Letras acentuadas e palavras vazias típicas do português (não aparecem em texto inglês).
PT_ACCENTS = re.compile(r"[ãõçáéíóúâêô]", re.IGNORECASE)
PT_STOPWORDS = re.compile(r"\b(de|que|para|não|uma|os|das|dos|com|por)\b", re.IGNORECASE)

CODE_SPAN = re.compile(r"`[^`]*`")
LINK_TARGET = re.compile(r"\]\([^)]*\)")


def _prose(line: str) -> str:
    """Remove trechos entre crases e alvos de links, que não são prosa."""
    return LINK_TARGET.sub("]", CODE_SPAN.sub("", line))


def _unreleased(path: pathlib.Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith("## [Unreleased]"))
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## [")),
        len(lines),
    )
    return lines[start + 1 : end]


def _sections(lines: list[str]) -> dict[str, list[str]]:
    """Mapeia título de `### ` para os itens (`- `) da seção, na ordem do arquivo."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        if line.startswith("### "):
            current = line[4:].strip()
            sections[current] = []
        elif current is not None and line.startswith("- "):
            sections[current].append(line)
    return sections


def _section_titles(lines: list[str]) -> list[str]:
    return [line[4:].strip() for line in lines if line.startswith("### ")]


def _items(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("- ")]


def _item_texts(lines: list[str]) -> list[str]:
    """Itens com as continuações juntas, como texto único, para checagens de idioma."""
    blocks: list[str] = []
    for line in lines:
        if line.startswith("- "):
            blocks.append(line)
        elif blocks and line.startswith("  ") and line.strip():
            blocks[-1] += " " + line.strip()
    return blocks


def test_unreleased_structure_has_no_loose_lines() -> None:
    for path in (EN_PATH, PT_PATH):
        for number, line in enumerate(_unreleased(path), 1):
            if not line.strip():
                continue
            is_heading = line.startswith("## ") or line.startswith("### ")
            is_item = line.startswith("- ")
            is_continuation = line.startswith("  ")
            assert is_heading or is_item or is_continuation, (
                f"{path.name}: linha {number} de [Unreleased] não é título, item nem "
                f"continuação com 2+ espaços (1 espaço = continuação solta): {line!r}"
            )


def test_no_item_marker_glued_to_previous_text() -> None:
    # Um `- **` precedido de qualquer caractere que não seja espaço é um item colado na linha anterior.
    glued = re.compile(r"\S- \*\*")
    for path in (EN_PATH, PT_PATH):
        for number, line in enumerate(_unreleased(path), 1):
            assert not glued.search(_prose(line)), (
                f"{path.name}: linha {number} tem item colado ao texto anterior: {line[:120]!r}"
            )


def test_sections_and_item_counts_match_between_languages() -> None:
    en = _unreleased(EN_PATH)
    pt = _unreleased(PT_PATH)
    en_titles = _section_titles(en)
    pt_titles = _section_titles(pt)
    assert [SECTION_MAP[t] for t in en_titles] == pt_titles, (
        f"seções diferentes ou fora de ordem: en={en_titles} pt={pt_titles}"
    )
    en_sections = _sections(en)
    pt_sections = _sections(pt)
    for en_title, pt_title in SECTION_MAP.items():
        if en_title not in en_sections:
            continue
        assert len(en_sections[en_title]) == len(pt_sections[pt_title]), (
            f"'{en_title}' tem {len(en_sections[en_title])} itens em inglês e "
            f"{len(pt_sections[pt_title])} em português"
        )


def test_english_changelog_has_no_portuguese_items() -> None:
    for item in _item_texts(_unreleased(EN_PATH)):
        prose = _prose(item)
        assert not PT_ACCENTS.search(prose), f"item em português em CHANGELOG.md: {item[:120]!r}"
        stopwords = PT_STOPWORDS.findall(prose)
        assert len(stopwords) < 3, f"item em português em CHANGELOG.md: {item[:120]!r} ({stopwords})"


def test_portuguese_items_are_in_portuguese() -> None:
    # Itens do arquivo em português devem ter ao menos uma marca do idioma (acento ou palavra vazia).
    for item in _item_texts(_unreleased(PT_PATH)):
        prose = _prose(item)
        assert PT_ACCENTS.search(prose) or PT_STOPWORDS.search(prose), (
            f"item sem marca de português em CHANGELOG.pt-BR.md: {item[:120]!r}"
        )


def test_version_headings_match_between_languages() -> None:
    version = re.compile(r"^## \[([^\]]+)\]", re.MULTILINE)

    def versions(path: pathlib.Path) -> list[str]:
        return version.findall(path.read_text(encoding="utf-8"))

    assert versions(EN_PATH) == versions(PT_PATH)
