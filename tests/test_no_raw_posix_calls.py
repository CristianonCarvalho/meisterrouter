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


# Chamadas POSIX cruas que só podem aparecer em meister/osops/. Cada arquivo listado
# ainda não foi migrado; a lista só deve encolher. Quando um arquivo for migrado,
# remova-o daqui (o teste falha se a exceção ficar sem uso).
RAW_POSIX_ATTRIBUTES = {
    ("os", "killpg"),
    ("os", "getpgid"),
    ("signal", "SIGKILL"),
}
PENDING_MIGRATION_EXCEPTIONS = {
    "meister/herdr/bridge.py": {("os", "killpg"), ("os", "getpgid")},
}


def _raw_posix_attributes(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            key = (node.value.id, node.attr)
            if key in RAW_POSIX_ATTRIBUTES:
                yield key, node.lineno


def test_no_raw_killpg_getpgid_sigkill_outside_osops():
    offenders = []
    used_by_exception: dict[str, set] = {}
    for path in _python_files_outside_osops():
        rel = path.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        allowed = PENDING_MIGRATION_EXCEPTIONS.get(rel, set())
        for key, lineno in _raw_posix_attributes(tree):
            if key in allowed:
                used_by_exception.setdefault(rel, set()).add(key)
                continue
            offenders.append(f"{rel}:{lineno} ({key[0]}.{key[1]})")
    assert not offenders, (
        "use meister.osops em vez de chamadas POSIX cruas; encontrado em: " + ", ".join(offenders)
    )
    stale = [
        rel
        for rel, keys in PENDING_MIGRATION_EXCEPTIONS.items()
        if used_by_exception.get(rel, set()) != keys
    ]
    assert not stale, (
        "exceções sem uso devem ser removidas de PENDING_MIGRATION_EXCEPTIONS: " + ", ".join(stale)
    )


def _imports_fcntl(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(alias.name == "fcntl" for alias in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "fcntl":
            return True
    return False


def test_no_fcntl_import_outside_osops():
    offenders = []
    for path in _python_files_outside_osops():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _imports_fcntl(tree):
            offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert not offenders, (
        "import fcntl só é permitido em meister/osops/; encontrado em: " + ", ".join(offenders)
    )
