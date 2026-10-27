import ast
import re
import subprocess
from typing import Any, Optional

COMPLEXITY_THRESHOLD: int = 10


def _walk_non_nested(root: ast.AST):
    """Yield all AST descendant nodes of root, without descending into nested functions."""
    stack = list(ast.iter_child_nodes(root))
    while stack:
        curr = stack.pop()
        if isinstance(curr, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        yield curr
        stack.extend(ast.iter_child_nodes(curr))


def _calculate_complexity(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Calculate cyclomatic complexity of a function without counting nested functions.

    Formula: 1 + number of if/elif, for, async for, while, except,
    each extra operand of and/or, each comprehension if clause, and each match case.
    with and assert do not count.
    """
    comp = 1
    for node in _walk_non_nested(func_node):
        if isinstance(node, ast.If):
            comp += 1
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler)):
            comp += 1
        elif isinstance(node, ast.BoolOp):
            comp += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            comp += len(node.ifs)
        elif isinstance(node, ast.match_case):
            comp += 1
    return comp


def function_complexities(source: str) -> dict[str, tuple[int, int, int]]:
    """Parse python source and return a mapping of qualified function names to (complexity, start_line, end_line).

    Returns an empty dict if the source fails to parse.
    """
    try:
        tree = ast.parse(source)
    except Exception:
        return {}

    results: dict[str, tuple[int, int, int]] = {}

    def visit(node: ast.AST, scope: list[str]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qname = ".".join(scope + [node.name])
            start_line = node.lineno
            end_line = getattr(node, "end_lineno", node.lineno) or node.lineno
            comp = _calculate_complexity(node)
            results[qname] = (comp, start_line, end_line)

            new_scope = scope + [node.name]
            for child in ast.iter_child_nodes(node):
                visit(child, new_scope)
        elif isinstance(node, ast.ClassDef):
            new_scope = scope + [node.name]
            for child in ast.iter_child_nodes(node):
                visit(child, new_scope)
        else:
            for child in ast.iter_child_nodes(node):
                visit(child, scope)

    for child in ast.iter_child_nodes(tree):
        visit(child, [])

    return results


def _parse_diff_changed_lines(diff_text: str) -> dict[str, set[int]]:
    """Parse git diff -U0 output and return changed line numbers in head per file path."""
    files: dict[str, set[int]] = {}
    current_file: Optional[str] = None

    diff_prefix = "diff --git a/"
    for line in diff_text.splitlines():
        if line.startswith(diff_prefix):
            remainder = line[len(diff_prefix) :]
            idx = remainder.rfind(" b/")
            if idx != -1:
                b_path = remainder[idx + 3 :].rstrip("\t\r\n").strip('"')
            else:
                b_path = remainder.rstrip("\t\r\n").strip('"')
            current_file = b_path
            if current_file not in files:
                files[current_file] = set()
        elif line.startswith("+++ b/"):
            b_path = line[6:].rstrip("\t\r\n").strip('"')
            current_file = b_path
            if current_file not in files:
                files[current_file] = set()
        elif line.startswith("@@ ") and current_file is not None:
            m = re.search(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                new_start = int(m.group(3))
                new_count = int(m.group(4)) if m.group(4) is not None else 1
                if new_count > 0:
                    for lno in range(new_start, new_start + new_count):
                        files[current_file].add(lno)
                else:
                    files[current_file].add(max(1, new_start))

    return files


def changed_function_report(repo_path: str, base: str, head: str) -> Optional[dict[str, Any]]:
    """Analyze changed functions in python files between base and head.

    Returns the complexity event dict or None if no python file changed or git failed.
    Never raises an exception.
    """
    try:
        # Run git diff for python files with filter Added and Modified
        diff_cmd = ["git", "diff", "-U0", "--diff-filter=AM", base, head, "--", "*.py"]
        res = subprocess.run(
            diff_cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if res.returncode != 0:
            return None

        diff_output = res.stdout
        if not diff_output.strip():
            return None

        files_changed_lines = _parse_diff_changed_lines(diff_output)
        if not files_changed_lines:
            return None

        functions_changed = 0
        over_threshold = 0
        worsened = 0
        max_complexity = 0
        worst = ""

        # For deterministic 'worst' selection, sort paths
        for path in sorted(files_changed_lines.keys()):
            changed_lines = files_changed_lines[path]

            # Read head source
            show_head = subprocess.run(
                ["git", "show", f"{head}:{path}"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if show_head.returncode != 0:
                return None
            after_source = show_head.stdout

            # Read base source (empty string if file was added)
            show_base = subprocess.run(
                ["git", "show", f"{base}:{path}"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            before_source = show_base.stdout if show_base.returncode == 0 else ""

            before_funcs = function_complexities(before_source)
            after_funcs = function_complexities(after_source)

            for func_name, (curr_comp, start_line, end_line) in after_funcs.items():
                is_new = func_name not in before_funcs
                touches_changed_lines = any(start_line <= line <= end_line for line in changed_lines)

                if is_new or touches_changed_lines:
                    functions_changed += 1

                    if curr_comp > COMPLEXITY_THRESHOLD:
                        over_threshold += 1

                    if is_new:
                        if curr_comp > COMPLEXITY_THRESHOLD:
                            worsened += 1
                    else:
                        prev_comp = before_funcs[func_name][0]
                        if curr_comp > prev_comp:
                            worsened += 1

                    if curr_comp > max_complexity:
                        max_complexity = curr_comp
                        worst = f"{path}::{func_name}"

        return {
            "functions_changed": functions_changed,
            "over_threshold": over_threshold,
            "worsened": worsened,
            "max_complexity": max_complexity,
            "worst": worst,
        }
    except Exception:
        return None
