import json
import os
import sys
from pathlib import Path
from typing import Optional

from meister.config import load_config
from meister.gate import DeterministicGate

_PRINT_ENV = "import os, sys; print(sys.argv[1] + '=' + os.environ.get(sys.argv[1], '<unset>'))"


def _gate(root: Path, variable: str, env: Optional[dict] = None) -> DeterministicGate:
    config_path = root / "meister.config.yaml"
    run = [sys.executable, "-c", _PRINT_ENV, variable]
    config_path.write_text(
        "environment:\n  install_dependencies: false\n"
        "gate:\n"
        + (f"  env: {json.dumps(env)}\n" if env is not None else "")
        + "  commands:\n"
        + "    - name: show\n"
        + f"      run: {json.dumps(run)}\n"
    )
    return DeterministicGate(str(root), load_config(str(config_path)))


def _value(result, variable: str) -> str:
    assert result.passed, result.output
    for line in result.output.splitlines():
        if line.startswith(variable + "="):
            return line[len(variable) + 1:]
    raise AssertionError(result.output)


def test_gate_env_reaches_command(tmp_path, monkeypatch):
    monkeypatch.delenv("MEISTER_TEST_GATE_ENV", raising=False)
    gate = _gate(tmp_path, "MEISTER_TEST_GATE_ENV", {"MEISTER_TEST_GATE_ENV": "hello"})
    assert _value(gate.run_verification_ex(), "MEISTER_TEST_GATE_ENV") == "hello"


def test_java_home_bin_goes_first_in_path(tmp_path):
    java_home = tmp_path / "jdk"
    (tmp_path / "node_modules" / ".bin").mkdir(parents=True)
    gate = _gate(tmp_path, "PATH", {"JAVA_HOME": str(java_home)})
    entries = _value(gate.run_verification_ex(), "PATH").split(os.pathsep)
    assert entries[0] == os.path.join(str(java_home), "bin")
    assert entries[1] == str(tmp_path / "node_modules" / ".bin")
    assert os.pathsep.join(entries[2:]) == os.environ.get("PATH", "")


def test_without_gate_env_path_and_environment_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("JAVA_HOME", str(tmp_path / "inherited-jdk"))
    gate = _gate(tmp_path, "PATH")
    assert gate.config.gate.env == {}
    path = _value(gate.run_verification_ex(), "PATH")
    assert path == os.environ.get("PATH", "")
    assert os.path.join(str(tmp_path / "inherited-jdk"), "bin") not in path.split(os.pathsep)


def test_apply_gate_env_keeps_existing_path_entries(tmp_path):
    gate = _gate(tmp_path, "PATH", {"JAVA_HOME": "/opt/jdk8", "MAVEN_HOME": "/opt/maven"})
    env = {"PATH": os.pathsep.join(["/project/.venv/bin", "/project/node_modules/.bin", "/usr/bin"])}
    gate._apply_gate_env(env)
    assert env["MAVEN_HOME"] == "/opt/maven"
    assert env["PATH"].split(os.pathsep) == [
        os.path.join("/opt/jdk8", "bin"),
        "/project/.venv/bin",
        "/project/node_modules/.bin",
        "/usr/bin",
    ]


def test_auto_detected_runner_gets_gate_env(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_env.py").write_text(
        "import os\n\n"
        "def test_env():\n"
        "    assert os.environ['MEISTER_TEST_GATE_ENV'] == 'auto'\n"
        "    assert os.environ['PATH'].split(os.pathsep)[0] == os.path.join('/opt/jdk', 'bin')\n"
    )
    (tmp_path / "meister.config.yaml").write_text(
        "environment:\n  install_dependencies: false\n"
        "gate:\n"
        f"  python: {json.dumps(sys.executable)}\n"
        "  cache: false\n"
        "  flaky_retries: 0\n"
        f"  env: {json.dumps({'MEISTER_TEST_GATE_ENV': 'auto', 'JAVA_HOME': '/opt/jdk'})}\n"
    )
    gate = DeterministicGate(str(tmp_path), load_config(str(tmp_path / "meister.config.yaml")))
    result = gate.run_verification_ex()
    assert result.passed, result.output
