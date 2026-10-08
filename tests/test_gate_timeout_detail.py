import subprocess

from meister.config import GateCommand, load_config
from meister.gate import DeterministicGate, format_timeout_detail


def test_format_timeout_detail_includes_partial_stdout_string():
    exc = subprocess.TimeoutExpired("pytest", 5, output="running test_x\n")

    detail = format_timeout_detail("pytest", exc)

    assert "running test_x" in detail
    assert detail.startswith(f"[pytest] timeout: {exc}")


def test_format_timeout_detail_decodes_partial_stdout_bytes():
    exc = subprocess.TimeoutExpired("pytest", 5, output=b"running test_x \xff\n")

    detail = format_timeout_detail("pytest", exc)

    assert "running test_x �" in detail


def test_format_timeout_detail_includes_partial_stderr():
    exc = subprocess.TimeoutExpired(
        "pytest", 5, output="running test_x\n", stderr=b"stderr detail\n"
    )

    detail = format_timeout_detail("pytest", exc)

    assert "running test_x" in detail
    assert "stderr detail" in detail


def test_format_timeout_detail_handles_missing_output():
    exc = subprocess.TimeoutExpired("pytest", 5, output=None)

    assert format_timeout_detail("pytest", exc) == f"[pytest] timeout: {exc}"


def test_format_timeout_detail_keeps_only_last_lines():
    exc = subprocess.TimeoutExpired(
        "pytest",
        5,
        output="\n".join(f"line {number}" for number in range(25)),
    )

    detail = format_timeout_detail("pytest", exc, max_lines=20)

    assert "line 4" not in detail
    assert "line 5" in detail
    assert detail.endswith("line 24")


def test_format_timeout_detail_limits_characters_from_the_start():
    exc = subprocess.TimeoutExpired("pytest", 5, output="old output\nlast test_x")

    detail = format_timeout_detail("pytest", exc, max_chars=9)

    assert detail.endswith("st test_x")
    assert "old output" not in detail


def test_configured_command_timeout_includes_output_and_remains_infrastructure_error(
    tmp_path, monkeypatch
):
    config = load_config()
    config.gate.commands = [GateCommand("tests", ["pytest"], 5, True)]
    gate = DeterministicGate(str(tmp_path), config)

    def timeout_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 5, output=b"running test_x\nFAILED test_x\n")

    monkeypatch.setattr("meister.gate.subprocess.run", timeout_run)

    result = gate.run_verification_ex()

    assert not result.passed
    assert result.infrastructure_error
    assert result.output.endswith("FAILED test_x")
    assert "running test_x" in result.output
