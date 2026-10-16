"""Tests for vitest-aware parsing in summarize_gate_failure."""

from meister.gate import summarize_gate_failure

VITEST_OUTPUT = """\
 ❯ src/app.test.tsx (2 tests | 2 failed) 45ms
 FAIL  src/app.test.tsx > App > renders title
AssertionError: expected 'Hello' to be 'Hi'
 ❯ src/app.test.tsx:12:5
<body>
  <div class="app">
    <h1>Hello</h1>
  </div>
</body>
 FAIL  src/app.test.tsx > App > shows save button
TestingLibraryElementError: Unable to find an element with the text: Save
<body>
  <button>Cancel</button>
</body>
 ❯ src/app.test.tsx:20:7

 Test Files  1 failed (1)
      Tests  2 failed (2)
"""

EXPECTED_JS_TESTS = [
    "src/app.test.tsx > App > renders title",
    "src/app.test.tsx > App > shows save button",
]

PYTEST_OUTPUT = """\
============================= test session starts ==============================
collected 2 items

tests/test_x.py F.                                                       [100%]

=================================== FAILURES ===================================
_______________________________ test_fails _______________________________
>       assert 1 == 2
E       assert 1 == 2
E        +  where 1 = int()
=========================== short test summary info ============================
FAILED tests/test_x.py::test_fails - assert 1 == 2
========================= 1 failed, 1 passed in 0.01s ========================="""


PYTEST_FINAL_LINE = "========================= 1 failed, 1 passed in 0.01s ========================="

PYTEST_EXCERPT_BEFORE_CHANGE = (
    "E       assert 1 == 2\n"
    "E        +  where 1 = int()\n"
    "\n"
    "=========================== short test summary info ============================\n"
    "FAILED tests/test_x.py::test_fails - assert 1 == 2\n"
    f"{PYTEST_FINAL_LINE}"
)


def test_vitest_failures_are_extracted_without_dom_dump():
    result = summarize_gate_failure(VITEST_OUTPUT)

    assert result["js_failed_tests"] == EXPECTED_JS_TESTS
    assert result["failed_tests"] == []
    assert "FAIL  src/app.test.tsx > App > renders title" in result["excerpt"]
    assert "FAIL  src/app.test.tsx > App > shows save button" in result["excerpt"]
    assert "AssertionError: expected 'Hello' to be 'Hi'" in result["excerpt"]
    assert "TestingLibraryElementError: Unable to find an element" in result["excerpt"]
    assert "Test Files  1 failed (1)" in result["excerpt"]
    assert "Tests  2 failed (2)" in result["excerpt"]
    assert "<div" not in result["excerpt"]
    assert "<body>" not in result["excerpt"]
    assert "<button>" not in result["excerpt"]


def test_vitest_ansi_colored_output_gives_same_result():
    colored = "\n".join(
        f"\x1b[31m{line}\x1b[39m" if line.strip() else line
        for line in VITEST_OUTPUT.splitlines()
    )
    colored = colored.replace("FAIL  ", "\x1b[1mFAIL\x1b[22m  ")

    assert summarize_gate_failure(colored) == summarize_gate_failure(VITEST_OUTPUT)
    assert summarize_gate_failure(colored)["js_failed_tests"] == EXPECTED_JS_TESTS
    assert summarize_gate_failure(colored)["failed_tests"] == []


def test_vitest_duplicate_failures_are_deduplicated():
    output = (
        "FAIL  src/a.test.ts > Suite > case one\n"
        "FAIL  src/a.test.ts > Suite > case one\n"
        "FAIL  src/a.test.ts > Suite > case two\n"
    )
    result = summarize_gate_failure(output)

    assert result["js_failed_tests"] == [
        "src/a.test.ts > Suite > case one",
        "src/a.test.ts > Suite > case two",
    ]


def test_vitest_only_fail_lines_count_as_failures():
    output = (
        "❯ src/a.test.ts > Suite > marker only\n"
        "× src/a.test.ts > Suite > cross only\n"
        "   × suite > passa so na segunda vez 3ms\n"
    )

    assert summarize_gate_failure(output)["js_failed_tests"] == []


def test_vitest_real_output_lists_failed_test_once_without_duration_line():
    output = """\
 ❯ src/flaky.test.ts (2 tests | 1 failed) 8ms
   × suite > passa so na segunda vez 3ms

⎯⎯⎯⎯⎯⎯⎯ Failed Tests 1 ⎯⎯⎯⎯⎯⎯⎯

 FAIL  src/flaky.test.ts > suite > passa so na segunda vez
AssertionError: expected 0 to be greater than or equal to 1
 ❯ src/flaky.test.ts:8:15

 Test Files  1 failed | 1 passed (2)
      Tests  1 failed | 1 passed (2)
"""
    result = summarize_gate_failure(output)

    assert result["js_failed_tests"] == ["src/flaky.test.ts > suite > passa so na segunda vez"]
    assert result["failed_tests"] == []


def test_vitest_fail_line_with_ansi_color_is_recognized():
    output = (
        "\x1b[31m   × suite > passa so na segunda vez 3ms\x1b[39m\n"
        "\x1b[1m FAIL \x1b[22m \x1b[31msrc/flaky.test.ts > suite > passa so na segunda vez\x1b[39m\n"
        "AssertionError: boom\n"
    )
    result = summarize_gate_failure(output)

    assert result["js_failed_tests"] == ["src/flaky.test.ts > suite > passa so na segunda vez"]


def test_vitest_fail_line_duration_suffix_is_removed():
    output = "FAIL  src/a.test.ts > Suite > slow case 1.2s\n"

    assert summarize_gate_failure(output)["js_failed_tests"] == ["src/a.test.ts > Suite > slow case"]


def test_vitest_js_failed_tests_capped_at_ten():
    output = "\n".join(
        f"FAIL  src/m.test.ts > Suite > case {i}" for i in range(15)
    )
    result = summarize_gate_failure(output)

    assert len(result["js_failed_tests"]) == 10
    assert result["js_failed_tests"][0] == "src/m.test.ts > Suite > case 0"
    assert result["js_failed_tests"][-1] == "src/m.test.ts > Suite > case 9"


def test_pytest_output_excerpt_unchanged_and_no_js_tests():
    result = summarize_gate_failure(PYTEST_OUTPUT)

    assert result == {
        "failed_tests": ["tests/test_x.py::test_fails"],
        "lint_errors": [],
        "excerpt": PYTEST_EXCERPT_BEFORE_CHANGE,
        "missing_dependencies": [],
        "js_failed_tests": [],
    }


def test_empty_output_returns_empty_js_list():
    assert summarize_gate_failure("")["js_failed_tests"] == []
    assert summarize_gate_failure("  \n ")["js_failed_tests"] == []
    assert summarize_gate_failure(None)["js_failed_tests"] == []


def test_vitest_output_without_recognizable_failure_lines_keeps_old_excerpt():
    lines = [f"noise line {i}" for i in range(1, 21)]
    lines.append("Error: boom without a failure header")
    output = "\n".join(lines)

    result = summarize_gate_failure(output)

    assert result["js_failed_tests"] == []
    assert result["excerpt"] == "\n".join(lines[-15:])
    assert "noise line 6" not in result["excerpt"]
    assert "noise line 7" in result["excerpt"]
