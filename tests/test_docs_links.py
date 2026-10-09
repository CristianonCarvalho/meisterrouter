"""Links relativos e âncoras da documentação (README e docs/, em inglês e em português) precisam existir."""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = sorted(
    [REPO_ROOT / "README.md", REPO_ROOT / "README.pt-BR.md", REPO_ROOT / "CHANGELOG.md", REPO_ROOT / "CHANGELOG.pt-BR.md"]
    + [p for p in (REPO_ROOT / "docs").rglob("*.md") if "superpowers" not in p.parts]
)
_FENCE = re.compile(r"^(`{3,}|~{3,}).*?^\1\s*$", re.M | re.S)
_LINK = re.compile(r"\]\(([^)\s]+)\)")


def _slug(heading: str) -> str:
    """Âncora do GitHub: minúsculas, sem pontuação (exceto hífen), espaços viram hífen."""
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower(), flags=re.UNICODE)
    return text.replace(" ", "-")


def _anchors(path: Path) -> set:
    text = _FENCE.sub("", path.read_text(encoding="utf-8"))
    return {_slug(h) for h in re.findall(r"^#{1,6} (.+)$", text, re.M)}


def _links(path: Path):
    text = _FENCE.sub("", path.read_text(encoding="utf-8"))
    for match in _LINK.finditer(text):
        target = match.group(1)
        if not target.startswith(("http://", "https://", "mailto:")):
            yield target


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_relative_links_and_anchors_resolve(doc):
    problems = []
    for target in _links(doc):
        file_part, _, anchor = target.partition("#")
        destination = (doc.parent / file_part).resolve() if file_part else doc
        if not destination.exists():
            problems.append(f"arquivo inexistente: {target}")
        elif anchor and destination.suffix == ".md" and anchor not in _anchors(destination):
            problems.append(f"âncora inexistente: {target}")
    assert not problems, f"{doc.relative_to(REPO_ROOT)}: {problems}"


def test_every_english_doc_has_a_portuguese_counterpart_or_is_listed():
    """O README e o CHANGELOG têm versão pt-BR; os docs em inglês têm o original em docs/pt-BR."""
    assert (REPO_ROOT / "README.pt-BR.md").is_file() and (REPO_ROOT / "CHANGELOG.pt-BR.md").is_file()
    english = {p.name for p in (REPO_ROOT / "docs").glob("*.md")}
    assert english == {
        "plan-format.md",
        "advanced.md",
        "diagrams.md",
        "execution-manual.md",
        "models-and-costs.md",
        "flow.md",
        "jev-data-sent.md",
    }
    portuguese = {p.name for p in (REPO_ROOT / "docs" / "pt-BR").glob("*.md")}
    assert len(portuguese) == len(english)
