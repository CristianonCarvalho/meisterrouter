"""Garante que chamadas POSIX cruas ficam restritas a ``meister/osops/``."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MEISTER_DIR = REPO_ROOT / "meister"
OSOPS_DIR = MEISTER_DIR / "osops"


def _python_files_outside_osops():
    for path in sorted(MEISTER_DIR.rglob("*.py")):
        if OSOPS_DIR in path.parents:
            continue
        yield path


def _is_os_kill_probe(node: ast.AST) -> bool:
    """Detecta ``os.kill(_, 0)``: sondagem de existência de processo."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not (
        isinstance(func, ast.Attribute)
        and func.attr == "kill"
        and isinstance(func.value, ast.Name)
        and func.value.id == "os"
    ):
        return False
    if len(node.args) < 2:
        return False
    signal_arg = node.args[1]
    return isinstance(signal_arg, ast.Constant) and signal_arg.value == 0


def test_no_os_kill_liveness_probe_outside_osops():
    offenders = []
    for path in _python_files_outside_osops():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if _is_os_kill_probe(node):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, (
        "os.kill(pid, 0) deve usar osops.pid_alive(pid); encontrado em: " + ", ".join(offenders)
    )
