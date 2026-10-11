"""Código novo não importa o motor pelo caminho antigo meister.herdr.{bridge,workers,dag,events}.

Os caminhos antigos continuam existindo como aliases (mesmo objeto de módulo), mas só os
arquivos da lista ALLOW podem citá-los. Quem precisa do motor importa de meister.engine.
"""

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LEGACY_NAMES = ("bridge", "workers", "dag", "events")
LEGACY_RE = re.compile(r"meister\.herdr\.(bridge|workers|dag|events)\b")

ALLOW = {
    "meister/herdr/bridge.py",
    "meister/herdr/workers.py",
    "meister/herdr/dag.py",
    "meister/herdr/events.py",
    "tests/test_engine_compat.py",
}


def _scan_file(path: Path) -> list[str]:
    rel = path.relative_to(REPO).as_posix()
    text = path.read_text(encoding="utf-8")
    hits: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if LEGACY_RE.search(line):
            hits.append(f"{rel}:{lineno}: {line.strip()}")
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError:
        return hits
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "meister.herdr" and any(a.name in LEGACY_NAMES for a in node.names):
                hits.append(f"{rel}:{node.lineno}: from meister.herdr import ...")
            elif node.module.startswith("meister.herdr.") and node.module.split(".")[-1] in LEGACY_NAMES:
                hits.append(f"{rel}:{node.lineno}: from {node.module} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("meister.herdr.") and alias.name.split(".")[-1] in LEGACY_NAMES:
                    hits.append(f"{rel}:{node.lineno}: import {alias.name}")
    return sorted(set(hits))


def test_no_legacy_engine_imports_outside_allow_list() -> None:
    files = sorted((REPO / "meister").rglob("*.py")) + sorted((REPO / "tests").rglob("*.py"))
    violations: list[str] = []
    for path in files:
        rel = path.relative_to(REPO).as_posix()
        if rel in ALLOW or "__pycache__" in path.parts:
            continue
        violations.extend(_scan_file(path))
    assert not violations, (
        "Importação pelo caminho antigo do motor (use meister.engine):\n  " + "\n  ".join(violations)
    )
