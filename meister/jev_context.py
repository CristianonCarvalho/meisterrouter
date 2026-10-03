"""Build a compact, task-focused context for Jev classification."""

from __future__ import annotations

import re
from typing import Any, List


_GLOBAL_CONSTRAINTS_START_RE = re.compile(r"(?m)^## Global Constraints[ \t]*\n")
_GLOBAL_CONSTRAINTS_END_RE = re.compile(r"(?m)^(?:Arquivos permitidos:|\*\*Files:\*\*)")
_BLANK_RUN_RE = re.compile(r"\n[ \t]*\n+")
_LIST_ITEM_RE = re.compile(r"^(?:[-*+]\s|\d+[.)]\s|\s)")


def _constraints_end(body: str, content_start: int) -> int:
    """Fim do bloco de Global Constraints dentro do corpo.

    Com marcador (`Arquivos permitidos:` ou `**Files:**`) vale o marcador. Sem marcador (tarefa sem escopo),
    o bloco termina na primeira linha em branco cuja proxima linha NAO e item de lista; assim o corpo da
    tarefa nunca e engolido pelas constraints.
    """
    marker = _GLOBAL_CONSTRAINTS_END_RE.search(body, content_start)
    if marker:
        return marker.start()
    for blank in _BLANK_RUN_RE.finditer(body, content_start):
        following = body[blank.end():]
        if following and not _LIST_ITEM_RE.match(following):
            return blank.end()
    return len(body)


def _as_lines(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    try:
        return [str(item) for item in value]
    except TypeError:
        return [str(value)]


def build_jev_context(task: dict, max_chars: int) -> str:
    """Format a plan task so Jev sees task details before any project-wide constraints."""
    description = str(task.get("description") or "")
    lines = description.splitlines()
    title = lines[0].strip() if lines and lines[0].strip() else str(task.get("id") or "")
    body = "\n".join(lines[1:])

    omitted_constraints = None
    constraints_start = _GLOBAL_CONSTRAINTS_START_RE.search(body)
    if constraints_start:
        content_start = constraints_start.end()
        end = _constraints_end(body, content_start)
        omitted_constraints = len(body[constraints_start.start():end].rstrip("\n"))
        body = body[:constraints_start.start()] + body[end:]

    body_lines = [
        line for line in body.splitlines()
        if not line.startswith("Arquivos permitidos:")
    ]
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)
    while body_lines and not body_lines[-1].strip():
        body_lines.pop()
    body = "\n".join(body_lines)

    target_files = _as_lines(task.get("target_files"))
    file_display = target_files[:60]
    if len(target_files) > 60:
        file_display.append(f"... +{len(target_files) - 60} arquivos")
    dependency_ids = _as_lines(task.get("depends_on"))
    header_lines = [
        f"Tarefa: {title}",
        f"Arquivos ({len(target_files)}): {', '.join(file_display)}",
        f"Depende de: {', '.join(dependency_ids) if dependency_ids else 'nenhuma'}",
    ]
    if omitted_constraints is not None:
        header_lines.append(
            f"Restricoes globais do projeto: omitidas ({omitted_constraints} caracteres)"
        )
    header = "\n".join(header_lines) + "\nCorpo:\n"

    full_context = header + body
    if len(full_context) <= max_chars or len(header) > max_chars:
        return full_context

    marker_template = "[... corpo truncado: {} caracteres omitidos]"
    retained = len(body)
    while True:
        omitted = len(body) - retained
        marker = marker_template.format(omitted)
        available = max_chars - len(header) - len(marker)
        if available >= retained:
            return header + body[:retained] + marker
        if available < 0:
            return header
        retained = available
