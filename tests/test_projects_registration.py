from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from meister.config import DashboardConfig
import meister.dashboard.launcher as launcher_mod
from meister.dashboard.launcher import ensure_dashboard
from meister.dashboard.server import start_server
from meister.projects import get_project, project_id, projects_file


def _make_project(root: Path) -> Path:
    project = root / "project"
    (project / ".meister").mkdir(parents=True)
    return project


def _registered(project: Path) -> bool:
    return get_project(project_id(str(project))) is not None


@pytest.fixture
def server_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("meister.dashboard.server.atexit.register", lambda *a, **k: None)
    monkeypatch.setattr("meister.dashboard.server.remove_state", lambda *a, **k: None)
    monkeypatch.setattr("meister.dashboard.server.app.run", lambda **kw: None)


def test_ensure_dashboard_registers_project(tmp_path: Path) -> None:
    project = _make_project(tmp_path)

    outcome = ensure_dashboard(project, DashboardConfig(open="never"))

    assert outcome.kind == "disabled"
    assert _registered(project)


def test_ensure_dashboard_registers_before_early_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _make_project(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(launcher_mod, "register_project", lambda path: calls.append(path))

    outcome = ensure_dashboard(project, DashboardConfig(open="never"), open_window=False)

    assert outcome.kind == "disabled"
    assert calls == [str(project.resolve())]


def test_ensure_dashboard_registration_failure_keeps_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _make_project(tmp_path)

    def boom(path: str) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(launcher_mod, "register_project", boom)

    outcome = ensure_dashboard(project, DashboardConfig(open="never"))

    assert outcome.kind == "disabled"
    assert outcome.url is None


def test_ensure_dashboard_non_project_directory_is_not_registered(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    outcome = ensure_dashboard(plain, DashboardConfig(open="never"))

    assert outcome.kind == "disabled"
    assert not Path(projects_file()).exists()


def test_start_server_registers_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server_stubs: None,
) -> None:
    project = _make_project(tmp_path)
    monkeypatch.chdir(project)

    start_server(port=5050)

    assert _registered(project)


def test_start_server_registration_failure_still_writes_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server_stubs: None,
) -> None:
    project = _make_project(tmp_path)
    monkeypatch.chdir(project)

    def boom(path: str) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr("meister.dashboard.server.register_project", boom)
    write_state = MagicMock()
    monkeypatch.setattr("meister.dashboard.server.write_state", write_state)

    start_server(port=5050)

    write_state.assert_called_once()
    assert write_state.call_args.args[0] == str(project.resolve())


def test_start_server_non_project_directory_is_not_registered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server_stubs: None,
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)
    write_state = MagicMock()
    monkeypatch.setattr("meister.dashboard.server.write_state", write_state)

    start_server(port=5050)

    write_state.assert_called_once()
    assert not Path(projects_file()).exists()
