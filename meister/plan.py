"""meister.plan — Canonical plan schema, validation, and adapter registry.

Exposes:
  - PlanError: exception for validation failures (accumulates messages).
  - load_plan(raw, *, allow_freeform=False): validate JSON; with
    allow_freeform=True fall through to parse_architect_plan.
  - ADAPTERS: dict[str, Callable] — adapter registry.
  - register_adapter(name): decorator to register a converter.
  - canonical_json(tasks): serialize to stable bytes for run_id derivation.
  - FORBIDDEN_KEYS: keys rejected from canonical plan dicts (D5).
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable


# Keys that must never appear in a canonical plan step (D5).
FORBIDDEN_KEYS: frozenset[str] = frozenset(
    ["command", "cwd", "worktree", "task_file", "result_file"]
)

# Allowed top-level keys in a canonical plan step (plus optional "timeout").
_REQUIRED_KEYS: frozenset[str] = frozenset(
    ["id", "description", "target_files", "depends_on"]
)
_ALLOWED_KEYS: frozenset[str] = _REQUIRED_KEYS | {"timeout"}

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_PATH_BAD_RE = re.compile(r"[\x00-\x1f\\]")

# Adapter registry: name → convert(text, *, deps, allow_unscoped) → list[dict]
ADAPTERS: dict[str, Callable[..., list[dict[str, Any]]]] = {}


def register_adapter(name: str) -> Callable:
    """Decorator: register a plan adapter function under *name*."""
    def decorator(fn: Callable) -> Callable:
        ADAPTERS[name] = fn
        return fn
    return decorator


class PlanError(ValueError):
    """Raised when plan validation fails; carries all accumulated messages."""

    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        super().__init__("\n".join(messages))


def _validate_path(path: str) -> str | None:
    """Return error string if *path* is invalid for target_files, else None."""
    if not isinstance(path, str):
        return f"target_files: expected string, got {type(path).__name__}"
    if not path:
        return "target_files: empty string"
    if path.startswith("/"):
        return f"target_files: absolute path forbidden: {path!r}"
    if path.startswith("\\"):
        return f"target_files: absolute path forbidden: {path!r}"
    # Normalize ./x → x for comparison only; original stored normalised
    norm = path
    if norm.startswith("./"):
        norm = norm[2:]
    parts = norm.replace("\\", "/").split("/")
    if ".." in parts:
        return f"target_files: '..' component forbidden: {path!r}"
    if _PATH_BAD_RE.search(path):
        return f"target_files: control character or backslash in path: {path!r}"
    return None


def _validate_tasks(tasks: list[Any]) -> list[str]:
    """Return a list of error strings for all validation failures in *tasks*."""
    errors: list[str] = []

    if not isinstance(tasks, list):
        return [f"plan must be a JSON array, got {type(tasks).__name__}"]
    if len(tasks) == 0:
        return ["plan must not be empty"]

    seen_ids: dict[str, int] = {}

    for idx, task in enumerate(tasks):
        loc = f"task[{idx}]"

        if not isinstance(task, dict):
            errors.append(f"{loc}: expected object, got {type(task).__name__}")
            continue

        task_id = task.get("id")
        if task_id and isinstance(task_id, str):
            loc = f"task[{idx}] (id={task_id!r})"

        # Unknown / forbidden keys (D5)
        extra = set(task.keys()) - _ALLOWED_KEYS
        for k in sorted(extra):
            errors.append(f"{loc}: unknown key {k!r} (forbidden: {sorted(FORBIDDEN_KEYS)})")

        # id
        if "id" not in task:
            errors.append(f"{loc}: missing required key 'id'")
        elif not isinstance(task["id"], str) or not _ID_RE.match(task["id"]):
            errors.append(
                f"{loc}: 'id' must match ^[A-Za-z0-9][A-Za-z0-9_-]{{0,63}}$, got {task.get('id')!r}"
            )
        else:
            tid = task["id"]
            if tid in seen_ids:
                errors.append(f"{loc}: duplicate id {tid!r} (first at task[{seen_ids[tid]}])")
            else:
                seen_ids[tid] = idx

        # description
        if "description" not in task:
            errors.append(f"{loc}: missing required key 'description'")
        elif not isinstance(task["description"], str) or not task["description"].strip():
            errors.append(f"{loc}: 'description' must be a non-empty string")

        # target_files
        if "target_files" not in task:
            errors.append(f"{loc}: missing required key 'target_files'")
        elif not isinstance(task["target_files"], list):
            errors.append(f"{loc}: 'target_files' must be a list")
        else:
            seen_paths: set[str] = set()
            for path in task["target_files"]:
                err = _validate_path(path)
                if err:
                    errors.append(f"{loc}: {err}")
                else:
                    norm = path[2:] if path.startswith("./") else path
                    if norm in seen_paths:
                        errors.append(f"{loc}: duplicate path in target_files: {path!r}")
                    seen_paths.add(norm)

        # depends_on
        if "depends_on" not in task:
            errors.append(f"{loc}: missing required key 'depends_on'")
        elif not isinstance(task["depends_on"], list):
            errors.append(f"{loc}: 'depends_on' must be a list")
        else:
            for dep in task["depends_on"]:
                if not isinstance(dep, str):
                    errors.append(f"{loc}: depends_on item must be string, got {type(dep).__name__}")

        # timeout (optional)
        if "timeout" in task:
            t = task["timeout"]
            if not isinstance(t, (int, float)) or isinstance(t, bool) or t <= 0:
                errors.append(f"{loc}: 'timeout' must be a positive number, got {t!r}")

    if errors:
        return errors

    # Second pass: depends_on references and cycle detection
    id_set = set(seen_ids.keys())

    for idx, task in enumerate(tasks):
        if not isinstance(task, dict):
            continue
        task_id = task.get("id")
        if not task_id or not isinstance(task_id, str):
            continue
        loc = f"task[{idx}] (id={task_id!r})"
        deps = task.get("depends_on")
        if not isinstance(deps, list):
            continue
        for dep in deps:
            if not isinstance(dep, str):
                continue
            if dep == task_id:
                errors.append(f"{loc}: self-reference in depends_on: {dep!r}")
            elif dep not in id_set:
                errors.append(f"{loc}: depends_on references unknown id {dep!r}")

    if errors:
        return errors

    # Cycle detection (Kahn's algorithm)
    # adj[tid] = list of tids that tid depends on (predecessors)
    adj: dict[str, list[str]] = {
        t["id"]: [d for d in t.get("depends_on", []) if isinstance(d, str)]
        for t in tasks
        if isinstance(t, dict) and isinstance(t.get("id"), str)
    }
    # rev[dep] = list of tids that depend on dep (successors)
    rev: dict[str, list[str]] = {tid: [] for tid in adj}
    # in_degree[tid] = number of predecessors (nodes tid must wait for)
    in_degree: dict[str, int] = {tid: 0 for tid in adj}
    for tid, deps in adj.items():
        for dep in deps:
            if dep in rev:
                rev[dep].append(tid)
            if dep in in_degree:
                in_degree[tid] += 1

    # BFS with zero-in-degree start
    from collections import deque
    queue: deque[str] = deque()
    for tid, deg in in_degree.items():
        if deg == 0:
            queue.append(tid)
    processed = 0
    while queue:
        node = queue.popleft()
        processed += 1
        for nxt in rev.get(node, []):
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)
    if processed != len(adj):
        cycle_nodes = [tid for tid, deg in in_degree.items() if deg > 0]
        errors.append(f"dependency cycle detected among: {cycle_nodes}")

    return errors


def validate_tasks(tasks: list[Any]) -> None:
    """Raise PlanError if *tasks* fails schema validation."""
    errs = _validate_tasks(tasks)
    if errs:
        raise PlanError(errs)


def canonical_json(tasks: list[dict[str, Any]]) -> str:
    """Return the canonical JSON string for a list of validated tasks.

    Uses sort_keys=True, ensure_ascii=False, no trailing newline, and
    compact separators — the same string used to derive run_id.
    """
    return json.dumps(tasks, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def load_plan(
    raw: str,
    *,
    allow_freeform: bool = False,
) -> list[dict[str, Any]]:
    """Parse and validate a plan.

    Parameters
    ----------
    raw:
        Raw input string — either canonical JSON or freeform text.
    allow_freeform:
        If True, fall through to ``parse_architect_plan`` when JSON fails,
        **without** validating the schema (legacy compatibility, D1).
        If False (default), only canonical JSON is accepted.

    Returns
    -------
    list[dict]
        List of task dicts; validated when allow_freeform=False.

    Raises
    ------
    PlanError
        When strict mode and the input is invalid.
    """
    if not raw or not raw.strip():
        if allow_freeform:
            return []
        raise PlanError(["plan is empty"])

    cleaned = raw.strip()

    # Try to parse as JSON first
    tasks: list[Any] | None = None
    json_error: str | None = None
    if cleaned.startswith("["):
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, list):
                tasks = parsed
        except json.JSONDecodeError as exc:
            json_error = str(exc)

    if tasks is None:
        if allow_freeform:
            # Legacy path: parse_architect_plan without schema validation
            from meister.herdr.bridge import parse_architect_plan
            return parse_architect_plan(raw)
        # Not JSON list → give helpful error
        hint = "Expected a JSON array ([{...}, ...])"
        if json_error:
            hint += f". JSON parse error: {json_error}"
        elif not cleaned.startswith("["):
            hint += f". Input starts with {cleaned[:20]!r} instead of '['"
        raise PlanError([hint])

    # Strict validation
    errs = _validate_tasks(tasks)
    if errs:
        raise PlanError(errs)

    return tasks
