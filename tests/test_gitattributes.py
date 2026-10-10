"""Finais de linha LF para scripts shell e templates, para checkouts no Windows."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_RULES = [
    "*.sh text eol=lf",
    "*.template text eol=lf",
    "bin/meister text eol=lf",
    "bin/cli.js text eol=lf",
]


def _rules() -> set[str]:
    lines = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    return {" ".join(line.split()) for line in lines if line.strip() and not line.lstrip().startswith("#")}


def test_gitattributes_forces_lf_for_shell_scripts_and_templates():
    rules = _rules()
    for rule in REQUIRED_RULES:
        assert rule in rules, f".gitattributes precisa conter: {rule}"
