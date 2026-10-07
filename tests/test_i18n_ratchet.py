"""Ratchet test against new Portuguese strings in codebase.

Counts lines with code string literals containing Portuguese accented letters:
[áéíóúâêôãõçÁÉÍÓÚÂÊÔÃÕÇ] outside meister/locales/, ignoring comments and docstrings.

MAINTENANCE INSTRUCTIONS:
- The BASELINE below records the current maximum allowed count of accented code lines per file.
- When existing Portuguese messages are migrated to meister/locales/ (phase 1), update the corresponding
  number in BASELINE DOWNWARDS to lock in the progress.
- BASELINE NUMBERS MUST NEVER BE INCREASED.
- Any new file in meister/ starts with a baseline of 0 and must not introduce accented string literals in code.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
import pytest

ACCENT_RE = re.compile(r"[áéíóúâêôãõçÁÉÍÓÚÂÊÔÃÕÇ]")

# Current baseline counts per file (generated in Phase 0).
# These numbers may only decrease over time, never increase.
BASELINE: dict[str, int] = {
    "meister/clean.py": 8,
    "meister/cli.py": 92,
    "meister/config.py": 39,
    "meister/env_setup.py": 2,
    "meister/gate.py": 10,
    "meister/herdr/bridge.py": 16,
    "meister/herdr/workers.py": 1,
    "meister/hooks.py": 8,
    "meister/jev.py": 23,
    "meister/models.py": 1,
    "meister/plan_adapters/superpowers.py": 1,
    "meister/plan_analysis.py": 7,
    "meister/report.py": 27,
    "meister/setup_cmd.py": 33,
    "meister/state.py": 10,
    "meister/timeline.py": 1,
    "meister/timeline_cli.py": 5,
    "meister/timeline_view.py": 10,
    "meister/worker.py": 10,
    "meister/worktree.py": 46,
}


def count_accented_code_lines(filepath: str | Path) -> int:
    """Count lines with string constants containing accents, ignoring docstrings and comments."""
    path_str = str(filepath)
    with open(path_str, "r", encoding="utf-8") as f:
        source = f.read()

    try:
        tree = ast.parse(source, filename=path_str)
    except SyntaxError:
        return 0

    # Identify all docstrings across module, class, and function scopes
    docstring_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                docstring_nodes.add(node.body[0].value)

    lines = source.splitlines()
    accented_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node in docstring_nodes:
                continue
            if ACCENT_RE.search(node.value):
                start = getattr(node, "lineno", 1) - 1
                end = getattr(node, "end_lineno", getattr(node, "lineno", 1))
                for line_idx in range(start, end):
                    if line_idx < len(lines) and ACCENT_RE.search(lines[line_idx]):
                        accented_lines.add(line_idx + 1)

    return len(accented_lines)


def test_i18n_ratchet():
    """Verify that no file exceeds its baseline and no new files introduce accented code strings."""
    repo_root = Path(__file__).resolve().parents[1]
    meister_dir = repo_root / "meister"

    violations: list[str] = []

    for root, dirs, files in os.walk(meister_dir):
        # Exclude locales package directory
        dirs[:] = [d for d in dirs if d != "locales"]
        for fname in sorted(files):
            if not fname.endswith(".py"):
                continue
            file_path = Path(root) / fname
            rel_path = file_path.relative_to(repo_root).as_posix()

            count = count_accented_code_lines(file_path)
            allowed = BASELINE.get(rel_path, 0)

            if count > allowed:
                violations.append(
                    f"{rel_path}: {count} accented code lines exceeds baseline of {allowed} (+{count - allowed})"
                )

    if violations:
        msg = (
            "Catraca de i18n reprovada! Foram introduzidas strings com caracteres acentuados fora de meister/locales/:\n"
            + "\n".join(f"  • {v}" for v in violations)
            + "\n\nAdicione novas mensagens a meister/locales/ ou use meister.i18n.t()."
        )
        pytest.fail(msg)


def test_ratchet_detects_accented_string_addition(tmp_path):
    """Mutation test: adding an accented code string causes count_accented_code_lines to increase."""
    sample_file = tmp_path / "sample.py"
    sample_file.write_text(
        '"""Docstring com acentuação é ignorada."""\n'
        '# Comentário com acentuação é ignorado\n'
        'x = "clean string"\n',
        encoding="utf-8",
    )
    assert count_accented_code_lines(sample_file) == 0

    # Mutate by adding an accented string literal in code
    sample_file.write_text(
        '"""Docstring com acentuação é ignorada."""\n'
        '# Comentário com acentuação é ignorado\n'
        'x = "mensagem com acentuação"\n',
        encoding="utf-8",
    )
    assert count_accented_code_lines(sample_file) == 1
