"""Piso de versão do Python: declarado, verificado e testado no CI com 3.10."""

from collections import namedtuple
from pathlib import Path

import yaml

from meister import setup_cmd

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON_FLOOR = "3.10"

FakeVersionInfo = namedtuple("FakeVersionInfo", "major minor micro releaselevel serial")


def _fake_version(major: int, minor: int, micro: int) -> FakeVersionInfo:
    return FakeVersionInfo(major, minor, micro, "final", 0)


def test_pyproject_declares_python_floor():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["requires-python"] == f">={PYTHON_FLOOR}"


def test_setup_py_declares_python_floor():
    text = (REPO_ROOT / "setup.py").read_text(encoding="utf-8")
    assert f'python_requires=">={PYTHON_FLOOR}"' in text


def test_check_python_rejects_3_9(monkeypatch):
    monkeypatch.setattr(setup_cmd.sys, "version_info", _fake_version(3, 9, 18))
    item = setup_cmd.check_python()
    assert item.status == "erro"
    assert "3.9.18" in item.message


def test_check_python_accepts_3_10(monkeypatch):
    monkeypatch.setattr(setup_cmd.sys, "version_info", _fake_version(3, 10, 0))
    item = setup_cmd.check_python()
    assert item.status == "ok"
    assert "3.10.0" in item.message


def test_ci_matrix_floor_is_python_floor():
    workflows = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    assert len(workflows) == 1, workflows
    ci = yaml.safe_load(workflows[0].read_text(encoding="utf-8"))
    versions = ci["jobs"]["test"]["strategy"]["matrix"]["python-version"]
    floor = min(versions, key=lambda v: tuple(int(p) for p in v.split(".")))
    assert floor == PYTHON_FLOOR
