"""Teste ponta a ponta do orchestrate com gate Maven real e harness de mentira, sem Herdr.

Prova a cadeia worker -> worktree -> gate Maven -> integração com ``runtime.host: process``.
Só roda com MEISTER_REAL_MAVEN=1 e um mvn disponível (PATH ou MAVEN_HOME); a primeira
execução baixa plugins do Maven Central, por isso cada teste ganha um limite de 600 s.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from meister.cli import main
from tests.conftest import configured_test_timeout
from tests.conftest import test_watchdog as _watchdog
from tests.test_e2e_process_host import E2EEnv, Tier, _git, _journal, _task
from tests.test_e2e_process_host import e2e  # noqa: F401
from tests.test_gate_maven_real import CALC, CALC_TEST, MAVEN_TIMEOUT, POM

pytestmark = pytest.mark.skipif(
    os.environ.get("MEISTER_REAL_MAVEN") != "1"
    or not (shutil.which("mvn") or os.environ.get("MAVEN_HOME")),
    reason="teste com Maven real: defina MEISTER_REAL_MAVEN=1 e tenha mvn no PATH ou MAVEN_HOME",
)

TASK_ID = "task-notes"
# O fixture e2e reduz o PATH aos wrappers falsos e ao sistema; o mvn e o java reais
# são localizados antes disso para voltarem ao PATH do teste.
_TOOL_DIRS = [
    str(Path(found).resolve().parent)
    for found in (shutil.which("mvn"), shutil.which("java"))
    if found
]


@pytest.fixture(autouse=True)
def timeout_each_test(request):
    """Substitui o watchdog do conftest: testes marcados com timeout ganham esse limite."""
    marker = request.node.get_closest_marker("timeout")
    seconds = configured_test_timeout()
    if marker and marker.args and seconds:
        seconds = max(seconds, float(marker.args[0]))
    with _watchdog(seconds, request.node.nodeid):
        yield


def _java_home():
    return os.environ.get("JAVA_HOME_8_X64") or os.environ.get("JAVA_HOME")


def _yaml_str(value: str) -> str:
    return "'%s'" % value.replace("'", "''")


def _write_test(repo: Path, expected: int) -> None:
    path = repo / "src" / "test" / "java" / "demo" / "CalcTest.java"
    path.write_text(CALC_TEST % expected, encoding="utf-8")


@pytest.fixture
def maven(e2e: E2EEnv, monkeypatch: pytest.MonkeyPatch) -> E2EEnv:  # noqa: F811
    """Troca o conteúdo do repositório do fixture e2e por um projeto Maven."""
    if _TOOL_DIRS:
        monkeypatch.setenv("PATH", os.pathsep.join([os.environ["PATH"], *_TOOL_DIRS]))
    repo = e2e.repo
    _git(repo, "rm", "-q", "app.py")
    (repo / "src" / "main" / "java" / "demo").mkdir(parents=True)
    (repo / "src" / "test" / "java" / "demo").mkdir(parents=True)
    (repo / "pom.xml").write_text(POM, encoding="utf-8")
    (repo / "src" / "main" / "java" / "demo" / "Calc.java").write_text(CALC, encoding="utf-8")
    _write_test(repo, 5)
    (repo / ".gitignore").write_text("target/\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "maven project")
    return e2e


def _orchestrate(env: E2EEnv):
    tier = Tier("tier_1", "codex", "gpt-6-luna", "write")
    env.install_cli(tier.harness, tier.mode)
    lines = [
        'version: "1.0"',
        "router:",
        "  mode: first",
        "runtime:",
        "  host: process",
        "concurrency:",
        '  isolation_mode: "git_worktree"',
        "environment:",
        "  install_dependencies: false",
        "gate:",
        "  repair_attempts: 0",
    ]
    java_home = _java_home()
    if java_home:
        lines += ["  env:", f"    JAVA_HOME: {_yaml_str(java_home)}"]
    lines += [
        "workers:",
        "  tier_order:",
        f"    - {{name: {tier.name}, harness: {tier.harness}, model: {tier.model}, max_retries: 0}}",
    ]
    config = env.tmp_path / "config.yaml"
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    plan = json.dumps([_task(TASK_ID, "notes")])
    return CliRunner().invoke(
        main, ["orchestrate", "-c", str(config), "--task", plan, "--quiet", "--no-open"]
    )


def _completed(env: E2EEnv) -> list[dict]:
    return [
        event
        for event in _journal(env)
        if (event.get("event_type") or event.get("event")) == "subtask_completed"
        and event.get("task_id") == TASK_ID
    ]


@pytest.mark.timeout(MAVEN_TIMEOUT)
def test_maven_gate_passes_and_integrates(maven: E2EEnv):
    result = _orchestrate(maven)

    assert result.exit_code == 0, result.output
    assert "notes.txt" in _git(maven.repo, "ls-tree", "--name-only", "main").split()
    assert _completed(maven)


@pytest.mark.timeout(MAVEN_TIMEOUT)
def test_maven_gate_fails_and_blocks_integration(maven: E2EEnv):
    _write_test(maven.repo, 6)
    _git(maven.repo, "commit", "-q", "-am", "wrong expectation")

    result = _orchestrate(maven)

    assert result.exit_code != 0, result.output
    assert "notes.txt" not in _git(maven.repo, "ls-tree", "--name-only", "main").split()
    assert not _completed(maven)
