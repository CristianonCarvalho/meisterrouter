import os
import subprocess
import tempfile

from meister.complexity import (
    COMPLEXITY_THRESHOLD,
    changed_function_report,
    function_complexities,
)


def _init_git_repo(path: str) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=path, check=True, capture_output=True)


def _commit_file(repo: str, filename: str, content: str, msg: str) -> str:
    filepath = os.path.join(repo, filename)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    subprocess.run(["git", "add", filename], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=repo, check=True, capture_output=True)
    res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8")
    return res.stdout.strip()


def test_complexity_threshold_constant():
    assert COMPLEXITY_THRESHOLD == 10


def test_function_complexities_exact_values():
    # 1. if/elif/else: base 1 + if(1) + elif(1) = 3
    source_if_elif = """def check_val(x):
    if x > 0:
        return 1
    elif x < 0:
        return -1
    else:
        return 0
"""
    comps = function_complexities(source_if_elif)
    assert "check_val" in comps
    comp, start, end = comps["check_val"]
    assert comp == 3
    assert start == 1
    assert end == 7

    # 2. um and: base 1 + and(1) = 2
    source_and = """def check_and(a, b):
    return a and b
"""
    comps = function_complexities(source_and)
    assert "check_and" in comps
    comp, start, end = comps["check_and"]
    assert comp == 2
    assert start == 1
    assert end == 2

    # 3. um laço: base 1 + for(1) = 2
    source_loop = """def iterate_items(items):
    for item in items:
        print(item)
"""
    comps = function_complexities(source_loop)
    assert "iterate_items" in comps
    comp, start, end = comps["iterate_items"]
    assert comp == 2
    assert start == 1
    assert end == 3


def test_function_complexities_other_constructs():
    # async for, while, except, BoolOp extra operands, comprehension if, match/case, with and assert (0)
    source = """async def complex_flow(items, flag):
    assert flag  # assert does not count
    with open("dummy") as f:  # with does not count
        pass
    while flag:  # +1
        pass
    async for item in items:  # +1
        pass
    try:
        pass
    except ValueError:  # +1
        pass
    except (TypeError, KeyError):  # +1
        pass
    res = [x for x in items if x > 0 if x < 100]  # +2 (two comprehension ifs)
    if (a and b and c) or d:  # +1 (if), +2 (and with 3 values), +1 (or with 2 values)
        pass
    match flag:
        case 1:  # +1
            pass
        case 2:  # +1
            pass
        case _:  # +1
            pass
"""
    comps = function_complexities(source)
    assert "complex_flow" in comps
    # Base 1 + while(1) + async for(1) + except(2) + comp_ifs(2) + if(1) + and_extra(2) + or_extra(1) + cases(3) = 14
    assert comps["complex_flow"][0] == 14


def test_function_complexities_nested_functions_and_classes():
    source = """class Processor:
    def process(self, x):
        if x > 0:
            pass
        def helper(y):
            if y:
                return 1
            elif y == 0:
                return 0
            return -1
        return helper(x)
"""
    comps = function_complexities(source)
    assert "Processor.process" in comps
    assert "Processor.process.helper" in comps

    # Processor.process: base 1 + if(1) = 2 (helper's body does not count towards process)
    assert comps["Processor.process"][0] == 2
    # Processor.process.helper: base 1 + if(1) + elif(1) = 3
    assert comps["Processor.process.helper"][0] == 3


def test_function_complexities_syntax_error():
    source = "def broken(x: invalid syntax :::"
    comps = function_complexities(source)
    assert comps == {}


def test_changed_function_report_diff_only_md():
    with tempfile.TemporaryDirectory() as td:
        _init_git_repo(td)
        base = _commit_file(td, "README.md", "# Title\n", "c1")
        head = _commit_file(td, "README.md", "# Title\nUpdated\n", "c2")

        report = changed_function_report(td, base, head)
        assert report is None


def test_changed_function_report_new_simple_function():
    with tempfile.TemporaryDirectory() as td:
        _init_git_repo(td)
        base = _commit_file(td, "setup.py", "# initial\n", "c1")
        code = """def hello():
    return "world"
"""
        head = _commit_file(td, "app.py", code, "c2")

        report = changed_function_report(td, base, head)
        assert report is not None
        assert report == {
            "functions_changed": 1,
            "over_threshold": 0,
            "worsened": 0,
            "max_complexity": 1,
            "worst": "app.py::hello",
        }


def test_changed_function_report_function_gains_two_ifs():
    with tempfile.TemporaryDirectory() as td:
        _init_git_repo(td)
        code_v1 = """def evaluate(val):
    return val
"""
        base = _commit_file(td, "eval.py", code_v1, "c1")

        code_v2 = """def evaluate(val):
    if val > 10:
        return 10
    if val < 0:
        return 0
    return val
"""
        head = _commit_file(td, "eval.py", code_v2, "c2")

        report = changed_function_report(td, base, head)
        assert report is not None
        assert report == {
            "functions_changed": 1,
            "over_threshold": 0,
            "worsened": 1,
            "max_complexity": 3,
            "worst": "eval.py::evaluate",
        }


def test_changed_function_report_unchanged_function():
    with tempfile.TemporaryDirectory() as td:
        _init_git_repo(td)
        code_v1 = """def untouched():
    return 42

def target():
    return 1
"""
        base = _commit_file(td, "utils.py", code_v1, "c1")

        code_v2 = """def untouched():
    return 42

def target():
    if True:
        return 1
    return 0
"""
        head = _commit_file(td, "utils.py", code_v2, "c2")

        report = changed_function_report(td, base, head)
        assert report is not None
        assert report["functions_changed"] == 1
        assert report["worst"] == "utils.py::target"


def test_changed_function_report_syntax_error_file():
    with tempfile.TemporaryDirectory() as td:
        _init_git_repo(td)
        base = _commit_file(td, "broken.py", "# empty\n", "c1")
        head = _commit_file(td, "broken.py", "def broken_code(: invalid", "c2")

        report = changed_function_report(td, base, head)
        assert report is not None
        assert report == {
            "functions_changed": 0,
            "over_threshold": 0,
            "worsened": 0,
            "max_complexity": 0,
            "worst": "",
        }


def test_changed_function_report_git_failure():
    assert changed_function_report("/non/existent/path/never", "HEAD~1", "HEAD") is None
    with tempfile.TemporaryDirectory() as td:
        _init_git_repo(td)
        assert changed_function_report(td, "invalid_base_sha", "invalid_head_sha") is None


def test_changed_function_report_new_function_over_threshold():
    with tempfile.TemporaryDirectory() as td:
        _init_git_repo(td)
        base = _commit_file(td, "init.py", "# init\n", "c1")

        # Create a new function with 11 branches (complexity = 12 > 10)
        # Should count as over_threshold and worsened
        lines = ["def complex_new(x):"]
        for i in range(11):
            lines.append(f"    if x == {i}: return {i}")
        lines.append("    return -1\n")
        code = "\n".join(lines)

        head = _commit_file(td, "heavy.py", code, "c2")

        report = changed_function_report(td, base, head)
        assert report is not None
        assert report == {
            "functions_changed": 1,
            "over_threshold": 1,
            "worsened": 1,
            "max_complexity": 12,
            "worst": "heavy.py::complex_new",
        }
