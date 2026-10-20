import hashlib
import json
import os
import pathlib
import re
import shutil

import pytest

from meister import projects
from meister.projects import (
    ProjectError,
    discover_projects,
    get_project,
    list_projects,
    project_id,
    projects_file,
    register_project,
    register_projects,
    validate_project_path,
)


def make_project(root, name, marker=".meister"):
    project = root / name
    project.mkdir(parents=True, exist_ok=True)
    if marker == ".meister":
        (project / marker).mkdir()
    else:
        (project / marker).write_text("workers: {}\n", encoding="utf-8")
    return project


def real(path):
    return os.path.realpath(str(path))


def test_project_id_is_12_hex_chars_of_sha1_of_real_path(tmp_path):
    project = make_project(tmp_path, "app")

    identifier = project_id(str(project))

    expected = hashlib.sha1(real(project).encode("utf-8")).hexdigest()[:12]
    assert identifier == expected
    assert re.fullmatch(r"[0-9a-f]{12}", identifier)


def test_project_id_is_stable_and_follows_symlinks(tmp_path):
    project = make_project(tmp_path, "app")
    link = tmp_path / "link"
    try:
        os.symlink(project, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("sistema sem links simbólicos")

    assert project_id(str(project)) == project_id(str(project))
    assert project_id(str(link)) == project_id(str(project))


def test_validate_returns_real_path_for_each_marker(tmp_path):
    with_meister = make_project(tmp_path, "a")
    with_config = make_project(tmp_path, "b", marker="meister.config.yaml")
    with_git = tmp_path / "c"
    (with_git / ".git").mkdir(parents=True)

    for project in (with_meister, with_config, with_git):
        assert validate_project_path(str(project)) == real(project)


def test_validate_refuses_relative_path():
    with pytest.raises(ProjectError) as info:
        validate_project_path("relativo/app")

    assert info.value.code == "not_absolute"


def test_validate_refuses_missing_path_and_file(tmp_path):
    missing = tmp_path / "nao_existe"
    a_file = tmp_path / "arquivo.txt"
    a_file.write_text("x", encoding="utf-8")

    for path in (missing, a_file):
        with pytest.raises(ProjectError) as info:
            validate_project_path(str(path))
        assert info.value.code == "not_a_directory"


def test_validate_refuses_directory_without_markers(tmp_path):
    empty = tmp_path / "vazia"
    empty.mkdir()

    with pytest.raises(ProjectError) as info:
        validate_project_path(str(empty))

    assert info.value.code == "not_a_project"


def test_validate_refuses_personal_folder_even_with_git(tmp_path, monkeypatch):
    home = tmp_path / "pessoal"
    (home / ".git").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    with pytest.raises(ProjectError) as info:
        validate_project_path(str(home))

    assert info.value.code == "forbidden"


def test_validate_refuses_filesystem_root():
    root = os.path.abspath(os.sep)

    with pytest.raises(ProjectError) as info:
        validate_project_path(root)

    assert info.value.code == "forbidden"


def test_register_twice_does_not_duplicate_and_updates_last_seen(tmp_path, monkeypatch):
    project = make_project(tmp_path, "app")
    stamps = iter(["2026-10-19T10:00:00+00:00", "2026-10-19T11:00:00+00:00"])
    monkeypatch.setattr(projects, "_now", lambda: next(stamps))

    first = register_project(str(project))
    second = register_project(str(project))

    assert first["id"] == second["id"] == project_id(str(project))
    assert first["name"] == "app"
    assert first["path"] == real(project)
    assert first["exists"] is True
    assert first["last_seen"] == "2026-10-19T10:00:00+00:00"
    assert second["last_seen"] == "2026-10-19T11:00:00+00:00"
    data = json.loads(pathlib.Path(projects_file()).read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert len(data["projects"]) == 1
    assert list_projects() == [second]


def test_registry_is_written_under_the_isolated_home(tmp_path):
    project = make_project(tmp_path, "app")

    register_project(str(project))

    home = pathlib.Path(os.environ["HOME"])
    assert pathlib.Path(projects_file()).is_relative_to(home)
    assert pathlib.Path(projects_file()).is_file()


def test_register_projects_refused_path_writes_nothing(tmp_path):
    good = make_project(tmp_path, "bom")
    bad = tmp_path / "vazia"
    bad.mkdir()

    with pytest.raises(ProjectError) as info:
        register_projects([str(good), str(bad)])

    assert info.value.code == "not_a_project"
    assert not pathlib.Path(projects_file()).exists()
    assert list_projects() == []


def test_get_project_finds_by_id_and_returns_none_when_unknown(tmp_path):
    project = make_project(tmp_path, "app")
    registered = register_project(str(project))

    found = get_project(registered["id"])
    assert found is not None
    assert found["path"] == real(project)
    assert get_project("000000000000") is None


def test_project_missing_from_disk_is_marked(tmp_path):
    project = make_project(tmp_path, "app")
    register_project(str(project))

    shutil.rmtree(project)

    [item] = list_projects()
    assert item["exists"] is False
    assert item["path"] == real(project)
    found = get_project(item["id"])
    assert found is not None
    assert found["exists"] is False


def test_missing_registry_lists_nothing():
    assert not pathlib.Path(projects_file()).exists()
    assert list_projects() == []


@pytest.mark.parametrize(
    "content",
    ["{nao é json", "[1, 2, 3]", json.dumps({"version": 1, "projects": "oops"})],
)
def test_corrupted_registry_lists_nothing_without_raising(content):
    path = pathlib.Path(projects_file())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    assert list_projects() == []


def test_register_over_corrupted_registry_replaces_it(tmp_path):
    path = pathlib.Path(projects_file())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{quebrado", encoding="utf-8")
    project = make_project(tmp_path, "app")

    register_project(str(project))

    assert [item["path"] for item in list_projects()] == [real(project)]


def test_discover_finds_markers_within_depth(tmp_path):
    base = tmp_path / "base"
    make_project(base, "raso")
    make_project(base / "grupo", "config", marker="meister.config.yaml")
    make_project(base / "d" / "e", "fundo")
    make_project(base / "g" / "h", "tambem_no_limite")
    make_project(base / "g" / "h" / "i", "fora_do_limite")

    found = discover_projects(str(base), max_depth=3)
    names = sorted(item["name"] for item in found)

    assert names == ["config", "fundo", "raso", "tambem_no_limite"]
    assert all(item["registered"] is False for item in found)
    assert all(re.fullmatch(r"[0-9a-f]{12}", item["id"]) for item in found)
    assert all(item["id"] == project_id(item["path"]) for item in found)


def test_discover_skips_ignored_directories(tmp_path):
    base = tmp_path / "base"
    for ignored in ("node_modules", ".git", ".venv", "venv", "dist", "build", "__pycache__", "worktrees"):
        make_project(base / ignored, "escondido")
    make_project(base, "visivel")

    found = discover_projects(str(base))

    assert [item["name"] for item in found] == ["visivel"]


def test_discover_does_not_follow_symlinks(tmp_path):
    base = tmp_path / "base"
    real_project = make_project(base, "app")
    try:
        os.symlink(real_project, base / "atalho", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("sistema sem links simbólicos")

    found = discover_projects(str(base))

    assert [item["path"] for item in found] == [real(real_project)]


def test_discover_respects_limit(tmp_path):
    base = tmp_path / "base"
    for index in range(5):
        make_project(base, f"p{index}")

    assert len(discover_projects(str(base), limit=2)) == 2


def test_discover_marks_registered_projects(tmp_path):
    base = tmp_path / "base"
    registered_project = make_project(base, "ja_registrado")
    make_project(base, "novo")
    register_project(str(registered_project))

    found = {item["name"]: item["registered"] for item in discover_projects(str(base))}

    assert found == {"ja_registrado": True, "novo": False}


def test_discover_refuses_relative_or_missing_base(tmp_path):
    with pytest.raises(ProjectError) as relative:
        discover_projects("relativo")
    with pytest.raises(ProjectError) as missing:
        discover_projects(str(tmp_path / "nao_existe"))

    assert relative.value.code == "not_absolute"
    assert missing.value.code == "not_a_directory"


def test_autouse_fixture_isolates_the_real_registry():
    home = pathlib.Path(os.environ["HOME"])

    assert os.environ["USERPROFILE"] == str(home)
    assert list(home.iterdir()) == []
    assert projects_file() == os.path.join(str(home), ".meister", "projects.json")
    assert not pathlib.Path(projects_file()).exists()
