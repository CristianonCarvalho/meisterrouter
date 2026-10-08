from unittest.mock import MagicMock, patch

from meister.gate import (
    DeterministicGate,
    detect_test_runner,
    evaluate_completion,
)


def test_detect_pytest_runner(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    gate = DeterministicGate(str(tmp_path))
    runner = gate.detect_test_runner()
    assert runner == "pytest"


def test_detect_pytest_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\nminversion = "6.0"\n')
    gate = DeterministicGate(str(tmp_path))
    assert gate.detect_test_runner() == "pytest"


def test_detect_pytest_from_conftest(tmp_path):
    (tmp_path / "conftest.py").write_text("# fixtures\n")
    assert detect_test_runner(str(tmp_path)) == "pytest"


def test_detect_vitest_config(tmp_path):
    (tmp_path / "vitest.config.ts").write_text("export default {}\n")
    gate = DeterministicGate(str(tmp_path))
    assert gate.detect_test_runner() == "vitest"


def test_detect_vitest_from_package_json(tmp_path):
    (tmp_path / "package.json").write_text('{"devDependencies": {"vitest": "^1.0.0"}}\n')
    assert detect_test_runner(str(tmp_path)) == "vitest"


def test_detect_npm_test_runner(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}\n')
    gate = DeterministicGate(str(tmp_path))
    assert gate.detect_test_runner() == "npm test"


def test_detect_cargo_runner(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "test"\n')
    gate = DeterministicGate(str(tmp_path))
    assert gate.detect_test_runner() == "cargo test"


def test_detect_no_runner(tmp_path):
    gate = DeterministicGate(str(tmp_path))
    assert gate.detect_test_runner() is None
    assert detect_test_runner(str(tmp_path)) is None


def test_detect_linters(tmp_path):
    gate = DeterministicGate(str(tmp_path))
    assert gate.detect_linters() == []

    (tmp_path / "ruff.toml").write_text("")
    assert "ruff" in gate.detect_linters()

    (tmp_path / ".eslintrc.json").write_text("{}")
    assert "eslint" in gate.detect_linters()


def test_run_verification_no_runner(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gate = DeterministicGate(str(tmp_path))
    passed, output = gate.run_verification()
    assert passed is False
    assert "Configure gate.commands" in output


def test_run_verification_pytest_success(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    gate = DeterministicGate(str(tmp_path))

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="3 passed in 0.12s", stderr="")
        passed, output = gate.run_verification()
        assert passed is True
        assert "3 passed" in output
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "pytest" in args[0] or "pytest" in args[-1]


def test_run_verification_test_failure(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    gate = DeterministicGate(str(tmp_path))

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="1 failed", stderr="AssertionError")
        passed, output = gate.run_verification()
        assert passed is False
        assert "1 failed" in output or "AssertionError" in output


def test_run_verification_linter_failure(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "ruff.toml").write_text("")
    gate = DeterministicGate(str(tmp_path))

    with patch("subprocess.run") as mock_run:
        # First call is linter check (failure), second is test runner (or linter halts early)
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout="ruff error: unused import", stderr=""),
            MagicMock(returncode=0, stdout="passed", stderr=""),
        ]
        passed, output = gate.run_verification()
        assert passed is False
        assert "ruff error" in output


def test_get_diff_summary(tmp_path):
    gate = DeterministicGate(str(tmp_path))
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=" file.py | 2 +-\n 1 file changed", stderr="")
        summary = gate.get_diff_summary()
        assert "file.py" in summary


def test_evaluate_completion():
    with patch("meister.gate.control_cycle") as mock_control:
        mock_control.return_value = {
            "task_id": "test-uuid",
            "action": "COMPLETE",
            "action_confidence": 0.95,
            "should_escalate": False,
            "escalate_probability": 0.05,
            "switch_implementer": False,
            "switch_implementer_probability": 0.05,
        }

        # Test function interface
        res = evaluate_completion("diff summary here", test_passed=True)
        assert res["action"] == "COMPLETE"
        mock_control.assert_called_with(
            diff_summary="diff summary here",
            test_result="pass",
            attempts=1,
            security_sensitive=False,
            model=None,
        )

        # Test failure case overrides COMPLETE to RETRY
        res_fail = evaluate_completion("diff summary here", test_passed=False)
        mock_control.assert_called_with(
            diff_summary="diff summary here",
            test_result="fail",
            attempts=1,
            security_sensitive=False,
            model=None,
        )
        assert res_fail["action"] == "RETRY"
        assert "Hard deterministic gate" in res_fail.get("override_reason", "")


def test_gate_evaluate_completion_defaults(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    gate = DeterministicGate(str(tmp_path))

    with patch.object(gate, "get_diff_summary", return_value="1 file changed"), \
         patch.object(gate, "run_verification", return_value=(True, "all passed")), \
         patch("meister.gate.control_cycle") as mock_control:

        mock_control.return_value = {"action": "COMPLETE"}
        res = gate.evaluate_completion()
        assert res["action"] == "COMPLETE"
        mock_control.assert_called_once_with(
            diff_summary="1 file changed",
            test_result="pass",
            attempts=1,
            security_sensitive=False,
            model=None,
        )
