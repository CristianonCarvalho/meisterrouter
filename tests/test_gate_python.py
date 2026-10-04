import json
import os
import shlex
from pathlib import Path
from typing import Optional

import pytest
from click.testing import CliRunner

from meister.cli import main
from meister.config import GateCommand, load_config, validate_config
from meister.gate import DeterministicGate


def _executable(path: Path, body: str = '#!/bin/sh\nexit 0\n') -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


def _fake_python(path: Path, exit_code: int = 0) -> Path:
    return _executable(
        path,
        '#!/bin/sh\nprintf "%s\\n" "$@" >> "$CALL_LOG"\n'
        f"exit {exit_code}\n",
    )


def _configured_gate(root: Path, commands: list[dict], python: Optional[str] = None):
    config_path = root / "meister.config.yaml"
    config_path.write_text(
        "environment:\n  install_dependencies: false\n"
        "gate:\n"
        + (f"  python: {json.dumps(python)}\n" if python is not None else "")
        + "  commands:\n"
        + "".join(
            f"    - name: {command['name']}\n"
            f"      run: {json.dumps(command['run'])}\n"
            f"      required: {command.get('required', True)}\n"
            f"      timeout_seconds: {command.get('timeout_seconds', 30)}\n"
            + (
                f"      ok_exit_codes: {json.dumps(command['ok_exit_codes'])}\n"
                if "ok_exit_codes" in command
                else ""
            )
            for command in commands
        )
    )
    return DeterministicGate(str(root), load_config(str(config_path)))


def test_resolve_python_prefers_explicit_config_over_project_venv(tmp_path):
    project_python = _fake_python(tmp_path / ".venv/bin/python")
    configured_python = _fake_python(tmp_path / "custom/python")
    config = load_config()
    config.gate.python = str(configured_python.relative_to(tmp_path))
    gate = DeterministicGate(str(tmp_path), config)

    assert gate._resolve_python() == (str(configured_python), None)
    assert project_python.exists()


@pytest.mark.parametrize("environment", [".venv", "venv"])
def test_resolve_python_finds_venv_at_project_root(tmp_path, environment):
    python = _fake_python(tmp_path / environment / "bin/python")
    gate = DeterministicGate(str(tmp_path))

    assert gate._resolve_python() == (str(python), None)


def test_resolve_python_falls_back_to_meister_python_with_warning(tmp_path):
    gate = DeterministicGate(str(tmp_path))

    python, warning = gate._resolve_python()

    assert python == os.sys.executable
    assert warning is not None
    assert "[AVISO] usando o Python do Meister" in warning


def test_explicit_python_missing_is_infrastructure_error(tmp_path):
    gate = _configured_gate(tmp_path, [{"name": "unit", "run": "echo ok"}], "missing/python")

    result = gate.run_verification_ex()

    assert not result.passed
    assert result.infrastructure_error
    assert "gate.python não encontrado" in result.output


def test_auto_mode_uses_project_python_for_pytest_and_ruff(tmp_path, monkeypatch):
    log = tmp_path / "argv.log"
    python = _fake_python(tmp_path / ".venv/bin/python")
    monkeypatch.setenv("CALL_LOG", str(log))
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "ruff.toml").write_text("")

    result = DeterministicGate(str(tmp_path)).run_verification_ex()

    assert result.passed
    assert log.read_text().splitlines() == [
        "-m",
        "ruff",
        "check",
        ".",
        "-m",
        "pytest",
    ]
    assert str(python) not in log.read_text()


def test_auto_mode_uses_python_from_project_root_not_worktree(tmp_path, monkeypatch):
    root = tmp_path / "project"
    worktree = tmp_path / "worktree"
    log = tmp_path / "argv.log"
    root_python = _fake_python(root / ".venv/bin/python")
    _fake_python(worktree / ".venv/bin/python", exit_code=1)
    monkeypatch.setenv("CALL_LOG", str(log))
    (worktree / "pytest.ini").write_text("[pytest]\n")

    result = DeterministicGate(str(root)).run_verification_ex(str(worktree))

    assert result.passed
    assert log.read_text().splitlines() == ["-m", "pytest"]
    assert root_python.exists()


@pytest.mark.parametrize(
    ("has_test_file", "exit_code", "passed"),
    [
        (False, 5, True),
        (True, 5, False),
        (False, 1, False),
        (True, 1, False),
    ],
)
def test_auto_pytest_exit_codes_only_skip_code_5_without_test_files(
    tmp_path, monkeypatch, has_test_file, exit_code, passed
):
    _fake_python(tmp_path / ".venv/bin/python", exit_code)
    monkeypatch.setenv("CALL_LOG", str(tmp_path / "argv.log"))
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    if has_test_file:
        (tmp_path / "test_x.py").write_text("")
    else:
        (tmp_path / ".venv/lib/test_ignored.py").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / ".venv/lib/test_ignored.py").write_text("")
        (tmp_path / "node_modules/test_ignored.py").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "node_modules/test_ignored.py").write_text("")

    result = DeterministicGate(str(tmp_path)).run_verification_ex()

    assert result.passed is passed
    if exit_code == 5 and not has_test_file:
        assert "[SKIPPED PYTEST] nenhum teste coletado" in result.output
        assert result.skipped == ["[SKIPPED PYTEST] nenhum teste coletado"]
    else:
        assert "[SKIPPED PYTEST]" not in result.output


def test_auto_python_fallback_warning_is_in_gate_output(tmp_path, monkeypatch):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    monkeypatch.setattr("meister.gate.sys.executable", "/meister/python")
    monkeypatch.setattr(
        "meister.gate.subprocess.run",
        lambda *args, **kwargs: type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )

    result = DeterministicGate(str(tmp_path)).run_verification_ex()

    assert result.passed
    assert "[AVISO] usando o Python do Meister (/meister/python)" in result.output


def test_configured_python_marker_and_venv_path_precedence(tmp_path, monkeypatch):
    venv_bin = tmp_path / ".venv/bin"
    log = tmp_path / "argv.log"
    python = _fake_python(venv_bin / "python")
    command_dir = tmp_path / "path with spaces"
    command_python = _fake_python(command_dir / "python")
    monkeypatch.setenv("CALL_LOG", str(log))
    gate = _configured_gate(
        tmp_path,
        [{"name": "script", "run": f"{shlex.quote(str(command_python))} -m pytest"}],
        str(command_python),
    )

    result = gate.run_verification_ex()

    assert result.passed
    assert log.read_text().splitlines() == ["-m", "pytest"]
    assert str(python) != str(command_python)


def test_configured_command_resolves_python_marker_with_spaces(tmp_path, monkeypatch):
    python = _fake_python(tmp_path / "python with spaces/python")
    log = tmp_path / "argv.log"
    monkeypatch.setenv("CALL_LOG", str(log))
    gate = _configured_gate(
        tmp_path,
        [{"name": "python", "run": "{python} -m pytest"}],
        str(python.relative_to(tmp_path)),
    )

    result = gate.run_verification_ex()

    assert result.passed
    assert log.read_text().splitlines() == ["-m", "pytest"]


def test_configured_path_uses_venv_before_node_modules_and_original(tmp_path, monkeypatch):
    venv_bin = tmp_path / ".venv/bin"
    _fake_python(venv_bin / "python")
    local_bin = tmp_path / "node_modules/.bin"
    local_bin.mkdir(parents=True)
    path_log = tmp_path / "path.log"
    _executable(
        venv_bin / "my-ruff",
        '#!/bin/sh\nprintf "%s" "$PATH" > "$PATH_LOG"\nexit 0\n',
    )
    monkeypatch.setenv("PATH_LOG", str(path_log))
    monkeypatch.setenv("PATH", "/original/path")
    gate = _configured_gate(tmp_path, [{"name": "ruff", "run": "my-ruff"}])

    result = gate.run_verification_ex()

    assert result.passed
    path_entries = path_log.read_text().split(os.pathsep)
    assert path_entries == [str(venv_bin), str(local_bin), "/original/path"]


@pytest.mark.parametrize("ok_codes", [None, [0]])
def test_configured_commands_reject_code_5_by_default(tmp_path, ok_codes):
    code = [{"name": "check", "run": "sh -c 'exit 5'"}]
    if ok_codes is not None:
        code[0]["ok_exit_codes"] = ok_codes
    gate = _configured_gate(tmp_path, code)

    result = gate.run_verification_ex()

    assert not result.passed


def test_configured_ok_exit_codes_accept_and_report_nonzero(tmp_path):
    gate = _configured_gate(
        tmp_path,
        [{"name": "pytest", "run": "sh -c 'exit 5'", "ok_exit_codes": [0, 5]}],
    )

    result = gate.run_verification_ex()

    assert result.passed
    assert "[NOTE pytest] rc=5 aceito por ok_exit_codes" in result.output


def test_configured_optional_failure_warns_but_unaccepted_required_failure_fails(tmp_path):
    optional = _configured_gate(
        tmp_path, [{"name": "optional", "run": "sh -c 'exit 2'", "required": False}]
    ).run_verification_ex()
    required = _configured_gate(
        tmp_path, [{"name": "required", "run": "sh -c 'exit 2'", "required": True}]
    ).run_verification_ex()

    assert optional.passed
    assert "[WARN optional]" in optional.output
    assert not required.passed


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("python", "''"),
        ("python", "0"),
        ("python", "[]"),
        ("ok_exit_codes", "[]"),
        ("ok_exit_codes", "[true]"),
        ("ok_exit_codes", '"x"'),
        ("ok_exit_codes", "0"),
    ],
)
def test_invalid_python_or_ok_exit_codes_report_config_issue(tmp_path, field, value):
    if field == "python":
        config_text = f"gate:\n  python: {value}\n"
        expected_path = "gate.python"
    else:
        config_text = (
            "gate:\n  commands:\n    - name: test\n      run: pytest\n"
            f"      ok_exit_codes: {value}\n"
        )
        expected_path = "gate.commands[0].ok_exit_codes"
    config_file = tmp_path / "invalid.yaml"
    config_file.write_text(config_text)

    issues = validate_config(load_config(str(config_file)))

    assert any(issue.level == "error" and issue.path == expected_path for issue in issues)


def test_gate_config_defaults_and_four_positional_gate_command(tmp_path):
    gate = load_config().gate
    command = GateCommand("a", "b", 1.0, True)

    assert gate.python is None
    assert command.ok_exit_codes == [0]
    assert load_config().gate.commands == []


def test_config_show_exposes_python_and_ok_exit_codes(tmp_path):
    config_file = tmp_path / "custom.yaml"
    config_file.write_text(
        "gate:\n  python: .venv/bin/python\n  commands:\n"
        "    - name: pytest\n      run: '{python} -m pytest'\n"
        "      ok_exit_codes: [0, 5]\n"
    )
    runner = CliRunner()

    text = runner.invoke(main, ["config", "show", "--config-path", str(config_file)])
    json_result = runner.invoke(
        main, ["config", "show", "--json", "--config-path", str(config_file)]
    )

    assert text.exit_code == 0
    assert "Python: .venv/bin/python" in text.output
    assert "ok_exit_codes=[0, 5]" in text.output
    assert json_result.exit_code == 0
    gate = json.loads(json_result.output)["gate"]
    assert gate["python"] == ".venv/bin/python"
    assert gate["commands"][0]["ok_exit_codes"] == [0, 5]
