"""meister.plan_template — Moldura de plano gerada por `meister plan new`.

O Markdown passa por `plan import` e `plan validate` sem edição; o JSON é o array canônico
(`id`, `description`, `target_files`, `depends_on`), sem chaves extras.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional, Union

MAX_CONSTRAINTS = 8
_RULE_FILES = ("CLAUDE.md", "AGENTS.md")
_LIST_ITEM_RE = re.compile(r"^\s{0,3}(?:[-*+]|\d{1,3}[.)])\s+(?:\[[ xX]\]\s+)?(.+?)\s*$")
_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_MIN_RULE_WORDS = 3

_TITLE = "# <Plan title>"
_GUIDE_COMMENT = "<!-- Format rules: run `meister plan guide`. -->"
_CONSTRAINT_PLACEHOLDER = "- <rule that applies to every task>"
_TASK_TITLE = "<short title>"
_TASK_BODY = "<What to deliver and how to test it. Keep it short.>"


def _check_count(tasks: int) -> None:
    if isinstance(tasks, bool) or not isinstance(tasks, int) or tasks < 1:
        raise ValueError(f"tasks must be a positive integer, got {tasks!r}")


def _example_files(k: int) -> list[str]:
    return [f"src/module_{k}.py", f"tests/test_module_{k}.py"]


def _as_bullet(rule: str) -> str:
    rule = rule.strip()
    return rule if rule.startswith("- ") else f"- {rule.lstrip('-* ').strip()}"


def render_markdown_template(tasks: int = 3, constraints: Optional[list[str]] = None) -> str:
    """Return a Markdown plan skeleton with *tasks* empty task blocks."""
    _check_count(tasks)
    rules = [_as_bullet(rule) for rule in constraints or [] if rule.strip()]
    lines = [_TITLE, "", _GUIDE_COMMENT, "", "## Global Constraints", ""]
    lines += rules or [_CONSTRAINT_PLACEHOLDER]
    for k in range(1, tasks + 1):
        source, test = _example_files(k)
        lines += [
            "",
            f"### Task {k}: {_TASK_TITLE}",
            "",
            "**Files:**",
            f"- Create: `{source}`",
            f"- Test: `{test}`",
        ]
        if k > 1:
            lines += ["", f"**Depends on:** Task {k - 1}"]
        lines += ["", _TASK_BODY]
    return "\n".join(lines) + "\n"


def render_json_template(tasks: int = 3) -> str:
    """Return the canonical JSON plan skeleton (an array of *tasks* tasks)."""
    _check_count(tasks)
    plan: list[dict[str, Any]] = [
        {
            "id": f"task_{k}",
            "description": f"Task {k}: {_TASK_TITLE}\n\n{_TASK_BODY}",
            "target_files": _example_files(k),
            "depends_on": [f"task_{k - 1}"] if k > 1 else [],
        }
        for k in range(1, tasks + 1)
    ]
    return json.dumps(plan, indent=2, ensure_ascii=False) + "\n"


def _list_rules(text: str) -> list[str]:
    rules: list[str] = []
    fence: Optional[str] = None
    for line in text.splitlines():
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        match = _LIST_ITEM_RE.match(line)
        if match and len(match.group(1).split()) >= _MIN_RULE_WORDS:
            rules.append(f"- {match.group(1)}")
    return rules


def project_constraints(cwd: Union[str, Path]) -> list[str]:
    """Return up to 8 list-item rules found in CLAUDE.md and AGENTS.md of *cwd* (never invented)."""
    found: list[str] = []
    for name in _RULE_FILES:
        path = Path(cwd) / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for rule in _list_rules(text):
            if rule not in found:
                found.append(rule)
            if len(found) >= MAX_CONSTRAINTS:
                return found
    return found
