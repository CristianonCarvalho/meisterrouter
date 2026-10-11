"""Integração do gate Java/Maven com o Maven de verdade.

Só roda com MEISTER_REAL_MAVEN=1 e um mvn disponível (PATH ou MAVEN_HOME). A primeira
execução baixa plugins do Maven Central, por isso os testes que chamam o Maven ganham
um limite de 600 s em vez do MEISTER_TEST_TIMEOUT padrão.
"""

import os
import shutil
from pathlib import Path

import pytest

from meister.gate import DeterministicGate
from meister.i18n import t
from tests.conftest import configured_test_timeout
from tests.conftest import test_watchdog as _watchdog

MAVEN_TIMEOUT = 600

pytestmark = pytest.mark.skipif(
    os.environ.get("MEISTER_REAL_MAVEN") != "1"
    or not (shutil.which("mvn") or os.environ.get("MAVEN_HOME")),
    reason="teste com Maven real: defina MEISTER_REAL_MAVEN=1 e tenha mvn no PATH ou MAVEN_HOME",
)

POM = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>demo</groupId>
  <artifactId>calc</artifactId>
  <version>1.0-SNAPSHOT</version>
  <packaging>jar</packaging>
  <properties>
    <maven.compiler.source>1.8</maven.compiler.source>
    <maven.compiler.target>1.8</maven.compiler.target>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
  </properties>
  <dependencies>
    <dependency>
      <groupId>junit</groupId>
      <artifactId>junit</artifactId>
      <version>4.13.2</version>
      <scope>test</scope>
    </dependency>
  </dependencies>
</project>
"""

CALC = """package demo;

public class Calc {
    public int add(int a, int b) {
        return a + b;
    }
}
"""

CALC_TEST = """package demo;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class CalcTest {
    @Test
    public void addsTwoNumbers() {
        assertEquals(%d, new Calc().add(2, 3));
    }
}
"""


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


def _write_test(root: Path, expected: int) -> None:
    (root / "src" / "test" / "java" / "demo" / "CalcTest.java").write_text(CALC_TEST % expected)


def _project(root: Path) -> Path:
    (root / "src" / "main" / "java" / "demo").mkdir(parents=True)
    (root / "src" / "test" / "java" / "demo").mkdir(parents=True)
    (root / "pom.xml").write_text(POM)
    (root / "src" / "main" / "java" / "demo" / "Calc.java").write_text(CALC)
    _write_test(root, 5)
    (root / ".gitignore").write_text("target/\n")
    config = "environment:\n  install_dependencies: false\n"
    java_home = _java_home()
    if java_home:
        config += "gate:\n  env:\n    JAVA_HOME: '%s'\n" % java_home.replace("'", "''")
    (root / "meister.config.yaml").write_text(config)
    return root


@pytest.mark.timeout(MAVEN_TIMEOUT)
def test_real_maven_passes(tmp_path):
    project = _project(tmp_path / "project")
    gate = DeterministicGate(str(project))
    assert gate.detect_test_runner() == "maven"
    result = gate._run_verification_uncached(str(project))
    assert result.passed, result.output
    assert not result.infrastructure_error


@pytest.mark.timeout(MAVEN_TIMEOUT)
def test_real_maven_fails_on_wrong_expectation(tmp_path):
    project = _project(tmp_path / "project")
    _write_test(project, 6)
    result = DeterministicGate(str(project))._run_verification_uncached(str(project))
    assert not result.passed, result.output
    assert not result.infrastructure_error, result.output


def test_real_maven_missing_is_infrastructure_error(tmp_path, monkeypatch):
    project = _project(tmp_path / "project")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.delenv("MAVEN_HOME", raising=False)
    monkeypatch.delenv("M2_HOME", raising=False)
    monkeypatch.setenv("PATH", str(empty))
    result = DeterministicGate(str(project))._run_verification_uncached(str(project))
    assert not result.passed
    assert result.infrastructure_error
    assert result.output == t("engine.gate.maven_missing")
