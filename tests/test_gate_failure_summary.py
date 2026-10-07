"""Tests for summarize_gate_failure diagnostic parsing."""

from meister.gate import summarize_gate_failure


def test_summarize_pytest_real_output_three_failures():
    output = """
============================= test session starts ==============================
platform darwin -- Python 3.11.15, pytest-9.1.1
collected 10 items

tests/test_alpha.py .F.                                                  [ 30%]
tests/test_beta.py F.                                                    [ 50%]
tests/test_gamma.py ..F..                                                [100%]

=================================== FAILURES ===================================
___________________________________ test_one ___________________________________

    def test_one():
>       assert 1 == 2
E       AssertionError: assert 1 == 2

tests/test_alpha.py:15: AssertionError
___________________________________ test_two ___________________________________

    def test_two():
>       raise ValueError("invalid value supplied")
E       ValueError: invalid value supplied

tests/test_beta.py:22: ValueError
__________________________________ test_three __________________________________

    def test_three():
>       assert "expected" in "received"
E       AssertionError: assert 'expected' in 'received'

tests/test_gamma.py:40: AssertionError
=========================== short test summary info ============================
FAILED tests/test_alpha.py::test_one - AssertionError: assert 1 == 2
FAILED tests/test_beta.py::test_two - ValueError: invalid value supplied
FAILED tests/test_gamma.py::test_three - AssertionError: assert 'expected' in 'received'
========================= 3 failed, 7 passed in 0.45s ==========================
"""
    result = summarize_gate_failure(output)
    assert result["failed_tests"] == [
        "tests/test_alpha.py::test_one",
        "tests/test_beta.py::test_two",
        "tests/test_gamma.py::test_three",
    ]
    assert result["lint_errors"] == []
    assert len(result["excerpt"]) <= 1500
    assert "short test summary info" in result["excerpt"]
    assert "AssertionError: assert 1 == 2" in result["excerpt"]


def test_summarize_ruff_and_mypy_lint_errors():
    output = """
[RUFF]
meister/bridge.py:12:1: F401 `sys` imported but unused
meister/bridge.py:45:80: E501 Line too long (88 > 79)
[MYPY]
meister/gate.py:50: error: Incompatible return value type (got "int", expected "str")
meister/gate.py:55:10: error: Argument 1 to "check" has incompatible type "None"; expected "str"
"""
    result = summarize_gate_failure(output)
    assert result["failed_tests"] == []
    assert len(result["lint_errors"]) == 4
    assert result["lint_errors"][0] == "meister/bridge.py:12:1: F401 `sys` imported but unused"
    assert result["lint_errors"][1] == "meister/bridge.py:45:80: E501 Line too long (88 > 79)"
    assert result["lint_errors"][2] == 'meister/gate.py:50: error: Incompatible return value type (got "int", expected "str")'
    assert result["lint_errors"][3] == 'meister/gate.py:55:10: error: Argument 1 to "check" has incompatible type "None"; expected "str"'
    assert len(result["excerpt"]) <= 1500
    assert "F401" in result["excerpt"]
    assert "Incompatible return value type" in result["excerpt"]


def test_summarize_empty_and_no_pattern():
    # Empty string and whitespace
    assert summarize_gate_failure("") == {"failed_tests": [], "lint_errors": [], "excerpt": ""}
    assert summarize_gate_failure("   \n\t  ") == {"failed_tests": [], "lint_errors": [], "excerpt": ""}

    # Non-matching text
    lines = [f"Generic error step {i}" for i in range(25)]
    output = "\n".join(lines)
    result = summarize_gate_failure(output)
    assert result["failed_tests"] == []
    assert result["lint_errors"] == []
    # Contains the last 15 non-empty lines
    assert "Generic error step 24" in result["excerpt"]
    assert "Generic error step 10" in result["excerpt"]
    assert "Generic error step 5" not in result["excerpt"]
    assert len(result["excerpt"]) <= 1500

    # Handles weird input without raising
    assert summarize_gate_failure(None) == {"failed_tests": [], "lint_errors": [], "excerpt": ""}


def test_summarize_over_twenty_failures_capped():
    lines = [f"FAILED tests/test_mod.py::test_case_{i} - AssertionError: fail" for i in range(30)]
    output = "=========================== short test summary info ============================\n" + "\n".join(lines)
    result = summarize_gate_failure(output)
    assert len(result["failed_tests"]) == 20
    assert result["failed_tests"][0] == "tests/test_mod.py::test_case_0"
    assert result["failed_tests"][19] == "tests/test_mod.py::test_case_19"


def test_summarize_deduplication():
    output = """
FAILED tests/test_foo.py::test_bar - AssertionError
FAILED tests/test_foo.py::test_bar - AssertionError
FAILED tests/test_foo.py::test_baz - AssertionError
"""
    result = summarize_gate_failure(output)
    assert result["failed_tests"] == [
        "tests/test_foo.py::test_bar",
        "tests/test_foo.py::test_baz",
    ]


def test_summarize_over_ten_lint_errors_capped():
    lines = [f"file.py:{i}:1: F40{i % 10} unused import" for i in range(15)]
    output = "\n".join(lines)
    result = summarize_gate_failure(output)
    assert len(result["lint_errors"]) == 10


def test_summarize_excerpt_max_1500_chars():
    huge_traceback = ("E   AssertionError: " + ("x" * 200) + "\n") * 50
    huge_summary = "\n".join(f"FAILED tests/test_big_{i}.py::test_foo - fail" for i in range(50))
    output = f"{huge_traceback}\nshort test summary info\n{huge_summary}"
    result = summarize_gate_failure(output)
    assert len(result["excerpt"]) <= 1500
