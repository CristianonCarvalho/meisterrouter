"""Teste estático: toda chamada de subprocess com text=True em meister/ define encoding.

Sem encoding, text=True decodifica pela página de código local (cp1252 no Windows):
estraga a saída UTF-8 dos harnesses (por exemplo as setas de 'Tokens ↑ 11700 • ↓ 5')
e bytes indefinidos em cp1252 levantam UnicodeDecodeError.
"""

from __future__ import annotations

import ast
from pathlib import Path

MEISTER_DIR = Path(__file__).resolve().parent.parent / "meister"

SUBPROCESS_FUNCS = {"run", "Popen", "check_output", "check_call"}
TEXT_KWARGS = {"text", "universal_newlines"}

# Pontos legítimos sem encoding: "arquivo_relativo:linha" -> motivo. Mantenha vazio
# enquanto possível; cada entrada precisa de um motivo explícito.
ALLOW: dict[str, str] = {}


def _subprocess_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Devolve (aliases do módulo subprocess, funções importadas por nome)."""
    modules: set[str] = set()
    funcs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    modules.add(alias.asname or "subprocess")
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                if alias.name in SUBPROCESS_FUNCS:
                    funcs.add(alias.asname or alias.name)
    return modules, funcs


def _is_subprocess_call(call: ast.Call, modules: set[str], funcs: set[str]) -> bool:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr in SUBPROCESS_FUNCS and isinstance(func.value, ast.Name) and func.value.id in modules
    if isinstance(func, ast.Name):
        return func.id in funcs
    return False


def _is_truthy_text_kwarg(kw: ast.keyword) -> bool:
    if kw.arg not in TEXT_KWARGS:
        return False
    # text=False explícito não decodifica; qualquer outra expressão pode ser verdadeira.
    return not (isinstance(kw.value, ast.Constant) and kw.value.value is False)


def _violations() -> list[str]:
    found: list[str] = []
    for path in sorted(MEISTER_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules, funcs = _subprocess_names(tree)
        if not modules and not funcs:
            continue
        rel = path.relative_to(MEISTER_DIR.parent).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not _is_subprocess_call(node, modules, funcs):
                continue
            kwargs = {kw.arg for kw in node.keywords if kw.arg}
            if not any(_is_truthy_text_kwarg(kw) for kw in node.keywords):
                continue
            if "encoding" in kwargs:
                continue
            found.append(f"{rel}:{node.lineno}")
    return found


def test_scanner_finds_subprocess_calls() -> None:
    # Sanidade: o scanner precisa enxergar as chamadas reais, senão o teste passa vazio.
    total = 0
    for path in MEISTER_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules, funcs = _subprocess_names(tree)
        total += sum(
            1 for node in ast.walk(tree) if isinstance(node, ast.Call) and _is_subprocess_call(node, modules, funcs)
        )
    assert total > 10


def test_scanner_flags_missing_encoding() -> None:
    src = (
        "import subprocess as sp\n"
        "from subprocess import Popen\n"
        "sp.run(['x'], text=True)\n"
        "Popen(['x'], universal_newlines=True)\n"
        "sp.run(['x'], text=True, encoding='utf-8', errors='replace')\n"
        "sp.run(['x'], text=False)\n"
    )
    tree = ast.parse(src)
    modules, funcs = _subprocess_names(tree)
    flagged = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _is_subprocess_call(node, modules, funcs)
        and any(_is_truthy_text_kwarg(kw) for kw in node.keywords)
        and "encoding" not in {kw.arg for kw in node.keywords}
    ]
    assert sorted(flagged) == [3, 4]


def test_text_mode_subprocess_calls_set_encoding() -> None:
    violations = [v for v in _violations() if v not in ALLOW]
    assert not violations, (
        "Chamadas de subprocess com text=True/universal_newlines=True sem encoding "
        "(passe encoding='utf-8', errors='replace'):\n" + "\n".join(violations)
    )


def test_allow_list_entries_are_still_needed() -> None:
    stale = sorted(set(ALLOW) - set(_violations()))
    assert not stale, f"Entradas de ALLOW sem violação correspondente: {stale}"
