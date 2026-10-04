"""meister.plan_adapters.superpowers — Adapter for the superpowers writing-plans format.

Converts a superpowers implementation plan (markdown, skill writing-plans §6.4.1)
to the canonical task list expected by meister.plan.validate_tasks.

Registered as adapter "superpowers" via meister.plan.register_adapter.

Public API
----------
convert(text, *, deps="sequential", allow_unscoped=False) -> list[dict]
    Pure, deterministic function — no LLM, no network, no I/O beyond the text.
    Raises meister.plan.PlanError on any ambiguity.
"""

from __future__ import annotations

import re
import sys
import fnmatch
from typing import Any

from meister.plan import PlanError, register_adapter

# ── Regex helpers ──────────────────────────────────────────────────────────────

_TASK_HEADER_RE = re.compile(r"^### Task (\d+): (.+)$", re.MULTILINE)
_GLOBAL_CONSTRAINTS_RE = re.compile(
    r"## Global Constraints\s*\n(.*?)(?=\n##\s|\n###\s+Task\s+\d+:|\Z)", re.DOTALL
)
_REVIEW_FOCUS_RE = re.compile(r"## Review Focus", re.IGNORECASE)
_FILES_BLOCK_RE = re.compile(r"\*\*Files:\*\*\s*\n((?:\s*-[^\n]*\n?)+)", re.MULTILINE)
_DEPENDS_ON_RE = re.compile(
    r"\*\*Depends on:\*\*\s*(.+?)$", re.MULTILINE | re.IGNORECASE
)
_FILE_BULLET_RE = re.compile(
    r"^\s*-\s*(Create|Modify|Test):\s*`([^`]+)`\s*$", re.IGNORECASE
)
# Strip line-range suffix :N or :N-M
_RANGE_SUFFIX_RE = re.compile(r":\d+(?:-\d+)?$")

_KNOWN_VERBS = frozenset(["create", "modify", "test"])


_FENCE_LINE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _fenced_ranges(text: str) -> list[tuple[int, int]]:
    """Return (start, end) offsets of CLOSED fenced code blocks (``` or ~~~, CommonMark rules).

    A fence closes only with the same character and at least the same length, so a
    4-backtick block can safely contain 3-backtick blocks. An opening fence that is
    never closed is ignored, so a stray fence cannot hide the rest of the plan.
    """
    ranges: list[tuple[int, int]] = []
    opened: tuple[str, int, int] | None = None  # (char, length, start offset)
    position = 0
    for line in text.splitlines(keepends=True):
        match = _FENCE_LINE_RE.match(line.rstrip("\r\n"))
        if opened is None:
            if match and not (match.group(1)[0] == "`" and "`" in match.group(2)):
                opened = (match.group(1)[0], len(match.group(1)), position)
        elif (
            match
            and match.group(1)[0] == opened[0]
            and len(match.group(1)) >= opened[1]
            and not match.group(2).strip()
        ):
            ranges.append((opened[2], position + len(line)))
            opened = None
        position += len(line)
    return ranges


def _in_ranges(offset: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in ranges)



def _parse_global_constraints(text: str) -> str:
    """Extract the raw '## Global Constraints' section (verbatim), or ''."""
    m = _GLOBAL_CONSTRAINTS_RE.search(text)
    if m:
        # Include the header too so it is self-contained in the description
        return "## Global Constraints\n" + m.group(1).rstrip()
    return ""


def _normalise_path(raw: str) -> str:
    """Strip backticks, spaces, line-range suffix, and normalise ./x → x."""
    path = raw.strip().strip("`").strip()
    path = _RANGE_SUFFIX_RE.sub("", path)
    if path.startswith("./"):
        path = path[2:]
    return path


def _validate_path_chars(path: str, loc: str) -> str | None:
    """Return error string if *path* violates canonical path rules."""
    if not path:
        return f"{loc}: empty path"
    if path.startswith("/") or path.startswith("\\"):
        return f"{loc}: absolute path forbidden: {path!r}"
    parts = path.replace("\\", "/").split("/")
    if ".." in parts:
        return f"{loc}: '..' component forbidden: {path!r}"
    if re.search(r"[\x00-\x1f\\]", path):
        return f"{loc}: control character or backslash in path: {path!r}"
    if path.count("[") != path.count("]"):
        return f"{loc}: invalid glob pattern (unclosed character class): {path!r}"
    for match in re.finditer(r"\[([^\]]*)\]", path):
        if not match.group(1) or match.group(1) in ("!", "^", "!!"):
            return f"{loc}: invalid glob pattern (empty character class): {path!r}"
    return None


_GENERATED_FILE_COMMANDS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"\bpnpm\s+(?:add|install)\b"), "pnpm add/install", "pnpm-lock.yaml"),
    (re.compile(r"\bnpm\s+install\b"), "npm install", "package-lock.json"),
    (re.compile(r"\byarn\s+add\b"), "yarn add", "yarn.lock"),
    (re.compile(r"\bdrizzle-kit\s+generate\b"), "drizzle-kit generate", "drizzle/*"),
    (re.compile(r"\bprisma\s+generate\b"), "prisma generate", "prisma/*"),
)


def _pattern_covers(generated: str, declared: list[str]) -> bool:
    candidates = [generated]
    if "*" in generated or "?" in generated or "[" in generated:
        base = generated.rsplit("/", 1)[0] if "/" in generated else ""
        candidates = [
            f"{base}/schema.ts" if base else "generated.ts",
            f"{base}/migrations/0000_init.sql" if base else "generated/migrations/0000_init.sql",
        ]
    for path in candidates:
        for pattern in declared:
            normalized = pattern.replace("\\", "/")
            if normalized.endswith("/"):
                normalized += "**"
            if fnmatch.fnmatchcase(path, normalized) or (
                "/" not in normalized and fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], normalized)
            ):
                if "*" not in generated and "?" not in generated and "[" not in generated:
                    return True
                if fnmatch.fnmatchcase(path, generated):
                    return True
    return False


def _warn_generated_files(name: str, body: str, files: list[str], tolerated: list[str]) -> None:
    declared = [*files, *tolerated]
    for matcher, command, generated in _GENERATED_FILE_COMMANDS:
        if matcher.search(body) and not _pattern_covers(generated, declared):
            print(
                f"warning: task {name!r}: o comando {command!r} pode gerar {generated!r}, "
                "não coberto por Files: ou scope.tolerated_files",
                file=sys.stderr,
            )


def _extract_files_block(task_body: str, loc: str) -> tuple[list[str], list[str]]:
    """Return (target_files, errors) from the **Files:** block in task_body."""
    m = _FILES_BLOCK_RE.search(task_body)
    if not m:
        return [], []  # Absence handled by caller

    block = m.group(1)
    paths: list[str] = []
    seen: set[str] = set()
    errors: list[str] = []

    for line in block.splitlines():
        bm = _FILE_BULLET_RE.match(line)
        if not bm:
            stripped = line.strip()
            if stripped and stripped.startswith("-"):
                # Unknown verb bullet
                parts = stripped.lstrip("-").strip().split(":", 1)
                verb = parts[0].strip().lower() if parts else ""
                if verb and verb not in _KNOWN_VERBS:
                    errors.append(f"{loc}: unknown Files: verb {verb!r} in line {stripped!r}")
            continue
        verb = bm.group(1).lower()
        raw_path = bm.group(2)
        path = _normalise_path(raw_path)
        err = _validate_path_chars(path, loc)
        if err:
            errors.append(err)
            continue
        if path not in seen:
            paths.append(path)
            seen.add(path)

    return paths, errors


def _parse_depends_on_line(line_val: str, task_num: int, all_nums: set[int], loc: str) -> tuple[list[str], list[str]]:
    """Parse '**Depends on:** Task 2, Task 5' or 'none'.

    Returns (dep_ids, errors).
    """
    val = line_val.strip()
    if val.lower() == "none":
        return [], []

    dep_ids: list[str] = []
    errors: list[str] = []
    # e.g. "Task 2, Task 5" or "task 2 and task 5"
    parts = re.split(r"[,\s]+", val, flags=re.IGNORECASE)
    nums_seen: set[int] = set()
    i = 0
    while i < len(parts):
        part = parts[i].strip().lower()
        if part in ("task", "and"):
            # next part should be the number
            i += 1
            if i < len(parts):
                num_str = parts[i].strip().rstrip(".,;")
                if num_str.isdigit():
                    n = int(num_str)
                    if n not in all_nums:
                        errors.append(f"{loc}: Depends on references unknown Task {n}")
                    elif n == task_num:
                        errors.append(f"{loc}: Depends on self-reference Task {n}")
                    elif n not in nums_seen:
                        nums_seen.add(n)
                        dep_ids.append(f"task_{n}")
        elif part.isdigit():
            n = int(part)
            if n not in all_nums:
                errors.append(f"{loc}: Depends on references unknown Task {n}")
            elif n == task_num:
                errors.append(f"{loc}: Depends on self-reference Task {n}")
            elif n not in nums_seen:
                nums_seen.add(n)
                dep_ids.append(f"task_{n}")
        i += 1

    return dep_ids, errors


def convert(
    text: str,
    *,
    deps: str = "sequential",
    allow_unscoped: bool = False,
    tolerated_files: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Convert a superpowers plan to canonical task dicts.

    Parameters
    ----------
    text:
        Full markdown text of the plan.
    deps:
        Dependency resolution mode: ``"sequential"`` (default, D3) or
        ``"files"`` (opt-in; intersects Create+Modify sets).
    allow_unscoped:
        If True, tasks with no **Files:** section get ``target_files=[]``
        and a warning on stderr.  If False (default), such tasks are an error.

    Returns
    -------
    list[dict]
        Canonical task dicts (not yet schema-validated — caller does that).

    Raises
    ------
    PlanError
        On any ambiguity or structural error.
    """
    if deps not in ("sequential", "files"):
        raise PlanError([f"unknown deps mode {deps!r}; choices: 'sequential', 'files'"])

    # ── 1. Find all task headers ───────────────────────────────────────────────
    fences = _fenced_ranges(text)
    headers = [m for m in _TASK_HEADER_RE.finditer(text) if not _in_ranges(m.start(), fences)]
    if not headers:
        raise PlanError(["no '### Task N: name' headings found in plan"])

    # ── 2. Check for duplicate task numbers ──────────────────────────────────
    errors: list[str] = []
    seen_nums: dict[int, int] = {}
    for m in headers:
        n = int(m.group(1))
        if n in seen_nums:
            errors.append(f"duplicate task number {n} (first at char {seen_nums[n]}, second at char {m.start()})")
        else:
            seen_nums[n] = m.start()
    if errors:
        raise PlanError(errors)

    all_nums: set[int] = set(seen_nums.keys())

    # ── 3. Extract Global Constraints section ─────────────────────────────────
    global_constraints = _parse_global_constraints(text)

    # ── 4. Split text into per-task bodies ───────────────────────────────────
    task_bodies: list[tuple[int, str, str]] = []  # (num, name, body)
    for i, m in enumerate(headers):
        num = int(m.group(1))
        name = m.group(2).strip()
        body_start = m.end()
        body_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        # Trim body at next ## section (non-task)
        body_raw = text[body_start:body_end]
        # Stop at '## ' section header (but not '### Task'), ignoring lines inside fenced
        # code blocks: a README/doc template in the task body may contain its own '## ' headings.
        for sec_match in re.finditer(r"\n## (?!#)", body_raw):
            if not _in_ranges(body_start + sec_match.start() + 1, fences):
                body_raw = body_raw[: sec_match.start()]
                break
        task_bodies.append((num, name, body_raw))

    # ── 5. Parse each task ───────────────────────────────────────────────────
    tasks: list[dict[str, Any]] = []
    # For --deps files: collect create+modify sets per task
    write_sets: dict[str, set[str]] = {}

    for num, name, body in task_bodies:
        task_id = f"task_{num}"
        loc = f"task_{num}"

        # ── target_files ──────────────────────────────────────────────────
        has_files_keyword = bool(_FILES_BLOCK_RE.search(body))
        file_paths, file_errors = _extract_files_block(body, loc)
        errors.extend(file_errors)
        _warn_generated_files(name, body, file_paths, tolerated_files or [])

        if not has_files_keyword or (has_files_keyword and not file_paths and not file_errors):
            # No **Files:** section at all, or **Files:** block is empty
            if not allow_unscoped:
                if not has_files_keyword:
                    errors.append(f"{loc}: no **Files:** section found (use --allow-unscoped to permit)")
                elif not file_paths:
                    errors.append(f"{loc}: **Files:** section is empty (use --allow-unscoped to permit)")
            else:
                print(f"warning: escopo desativado para {task_id}", file=sys.stderr)

        # Collect write set for --deps files
        create_modify: set[str] = set()
        if has_files_keyword:
            fm = _FILES_BLOCK_RE.search(body)
            if fm:
                for line in fm.group(1).splitlines():
                    bm = _FILE_BULLET_RE.match(line)
                    if bm and bm.group(1).lower() in ("create", "modify"):
                        create_modify.add(_normalise_path(bm.group(2)))
        write_sets[task_id] = create_modify

        # ── depends_on ──────────────────────────────────────────────────
        dm = _DEPENDS_ON_RE.search(body)
        explicit_deps: list[str] | None = None
        if dm:
            dep_ids, dep_errs = _parse_depends_on_line(dm.group(1), num, all_nums, loc)
            errors.extend(dep_errs)
            explicit_deps = dep_ids

        # ── description ─────────────────────────────────────────────────
        # "Task N: name" + global constraints + allowed files line + body verbatim
        allowed_files_line = ""
        if file_paths:
            allowed_files_line = f"Arquivos permitidos: {', '.join(file_paths)}"

        desc_parts = [f"Task {num}: {name}"]
        if global_constraints:
            desc_parts.append(global_constraints)
        if allowed_files_line:
            desc_parts.append(allowed_files_line)
        desc_parts.append(body.strip())
        description = "\n\n".join(desc_parts)

        if len(description) > 20_000:
            print(
                f"warning: task_{num} description is {len(description)} chars (> 20 000)",
                file=sys.stderr,
            )

        tasks.append({
            "id": task_id,
            "name_hint": name,  # temporary; removed below
            "description": description,
            "target_files": file_paths if file_paths else [],
            "depends_on": explicit_deps,  # None = resolve from --deps
            "_write_set": create_modify,  # temporary; removed below
        })

    if errors:
        raise PlanError(errors)

    # ── 6. Resolve depends_on from --deps if no explicit annotation ──────────
    task_ids_in_order = [f"task_{num}" for num, _, _ in task_bodies]
    for i, task in enumerate(tasks):
        if task["depends_on"] is not None:
            # Explicit annotation — keep as-is, already validated
            continue
        if deps == "sequential":
            task["depends_on"] = [task_ids_in_order[i - 1]] if i > 0 else []
        else:  # files
            my_all = task["_write_set"]
            m_files = set(task["target_files"])
            my_union = my_all | m_files  # Create+Modify+Test of this task
            deps_list: list[str] = []
            for j in range(i):
                other_id = task_ids_in_order[j]
                other_write = tasks[j]["_write_set"]
                if other_write & my_union:
                    deps_list.append(other_id)
            task["depends_on"] = deps_list

    # ── 7. Clean up temporary keys ───────────────────────────────────────────
    for task in tasks:
        task.pop("name_hint", None)
        task.pop("_write_set", None)

    return tasks


# Register adapter
register_adapter("superpowers")(convert)
