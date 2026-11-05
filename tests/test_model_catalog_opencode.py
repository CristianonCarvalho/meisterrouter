import json
import subprocess
from unittest import mock

import pytest

from meister import model_catalog


OPENCODE_OUTPUT = (
    "anthropic/claude-3-5-sonnet\n"
    "openai/gpt-4o\n"
    "opencode/big-pickle\n"
    "google/gemini-2.0-flash\n"
)


@pytest.fixture(autouse=True)
def isolated_sources(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("MEISTER_CONFIG_PATH", raising=False)
    monkeypatch.chdir(project)
    model_catalog.clear_model_catalog_cache()
    yield home
    model_catalog.clear_model_catalog_cache()


@pytest.fixture
def opencode_on_path(monkeypatch):
    monkeypatch.setattr(
        model_catalog.shutil,
        "which",
        lambda name: f"/fake/bin/{name}" if name == "opencode" else None,
    )


def _fake_opencode(stdout="", returncode=0):
    return mock.Mock(return_value=subprocess.CompletedProcess(["opencode", "models"], returncode, stdout, ""))


def _by_id(result):
    return {model["id"]: model for model in result["models"]}


def _statuses(result):
    return {source["name"]: source["status"] for source in result["sources"]}


def _write_codex_cache(home, content):
    path = home / ".codex"
    path.mkdir(parents=True, exist_ok=True)
    text = content if isinstance(content, str) else json.dumps(content)
    (path / "models_cache.json").write_text(text, encoding="utf-8")


def _codex_item(slug, display_name, visibility="list"):
    return {"slug": slug, "display_name": display_name, "visibility": visibility}


# --- opencode models ----------------------------------------------------------


def test_opencode_models_output_gives_id_null_label_and_source(opencode_on_path):
    with mock.patch.object(model_catalog.subprocess, "run", _fake_opencode(OPENCODE_OUTPUT)) as run:
        result = model_catalog.list_models("opencode")

    assert run.call_args.args[0] == ["opencode", "models"]
    assert run.call_args.kwargs["timeout"] == 10.0
    assert "shell" not in run.call_args.kwargs
    assert result["sources"] == [{"name": "opencode models", "status": "ok"}]
    assert [model["id"] for model in result["models"]] == [
        "anthropic/claude-3-5-sonnet",
        "openai/gpt-4o",
        "opencode/big-pickle",
        "google/gemini-2.0-flash",
    ]
    models = _by_id(result)
    assert models["openai/gpt-4o"]["label"] is None
    assert models["openai/gpt-4o"]["source"] == "opencode models"
    assert models["openai/gpt-4o"]["cost_per_m_tokens"] is None


def test_opencode_invalid_lines_are_discarded(opencode_on_path):
    stdout = (
        "Fetching available models...\n"
        "anthropic/claude-3-5-sonnet\n"
        "linha com espaco\n"
        "provider/modelo com espaco\n"
        "sem-barra\n"
        "/sem-provider\n"
        "sem-modelo/\n"
        "muitas/barras/no/id\n"
        "\n"
        "   \n"
        "openai/gpt-4o\n"
    )
    with mock.patch.object(model_catalog.subprocess, "run", _fake_opencode(stdout)):
        result = model_catalog.list_models("opencode")

    assert [model["id"] for model in result["models"]] == [
        "anthropic/claude-3-5-sonnet",
        "openai/gpt-4o",
    ]
    assert result["sources"] == [{"name": "opencode models", "status": "ok"}]


def test_opencode_missing_executable_is_unavailable_and_does_not_affect_other_sources(
    isolated_sources, monkeypatch
):
    _write_codex_cache(isolated_sources, {"models": [_codex_item("gpt-6-luna", "GPT-6-Luna")]})
    monkeypatch.setattr(model_catalog.shutil, "which", lambda name: None)

    with mock.patch.object(model_catalog.subprocess, "run") as run:
        opencode = model_catalog.list_models("opencode")
    codex = model_catalog.list_models("codex")

    assert not run.called
    assert _statuses(opencode) == {"opencode models": "unavailable", "config": "empty"}
    assert _statuses(codex) == {"codex cache": "ok"}
    assert [model["id"] for model in codex["models"]] == ["gpt-6-luna"]


def test_opencode_timeout_is_unavailable_and_does_not_affect_other_sources(
    opencode_on_path, isolated_sources
):
    _write_codex_cache(isolated_sources, {"models": [_codex_item("gpt-6-luna", "GPT-6-Luna")]})
    run = mock.Mock(side_effect=subprocess.TimeoutExpired(["opencode", "models"], 10))

    with mock.patch.object(model_catalog.subprocess, "run", run):
        opencode = model_catalog.list_models("opencode")
    codex = model_catalog.list_models("codex")

    assert _statuses(opencode) == {"opencode models": "unavailable", "config": "empty"}
    assert _statuses(codex) == {"codex cache": "ok"}
    assert [model["id"] for model in codex["models"]] == ["gpt-6-luna"]


@pytest.mark.parametrize("empty_stdout", ["", "   ", "\n", "\t \n"])
def test_opencode_empty_output_is_unavailable(opencode_on_path, empty_stdout):
    with mock.patch.object(model_catalog.subprocess, "run", _fake_opencode(empty_stdout)):
        result = model_catalog.list_models("opencode")

    assert _statuses(result) == {"opencode models": "unavailable", "config": "empty"}
    assert result["models"] == []


def test_opencode_duplicate_ids_appear_only_once(opencode_on_path):
    stdout = (
        "openai/gpt-4o\n"
        "anthropic/claude-3-5-sonnet\n"
        "openai/gpt-4o\n"
        "anthropic/claude-3-5-sonnet\n"
    )
    with mock.patch.object(model_catalog.subprocess, "run", _fake_opencode(stdout)):
        result = model_catalog.list_models("opencode")

    assert [model["id"] for model in result["models"]] == [
        "openai/gpt-4o",
        "anthropic/claude-3-5-sonnet",
    ]
    assert len(result["models"]) == 2


def test_opencode_nonzero_exit_is_unavailable(opencode_on_path):
    with mock.patch.object(model_catalog.subprocess, "run", _fake_opencode(OPENCODE_OUTPUT, returncode=1)):
        result = model_catalog.list_models("opencode")

    assert _statuses(result) == {"opencode models": "unavailable", "config": "empty"}
    assert result["models"] == []


def test_opencode_oserror_is_unavailable(opencode_on_path):
    run = mock.Mock(side_effect=OSError("permissao negada"))

    with mock.patch.object(model_catalog.subprocess, "run", run):
        result = model_catalog.list_models("opencode")

    assert _statuses(result) == {"opencode models": "unavailable", "config": "empty"}
    assert result["models"] == []


def test_opencode_config_fills_in_when_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(model_catalog.shutil, "which", lambda name: None)
    config_file = tmp_path / "project" / "meister.config.yaml"
    config_file.write_text(
        "workers:\n  tier_order:\n    - name: opencode_tier\n      harness: opencode\n      model: opencode/custom\n",
        encoding="utf-8",
    )
    result = model_catalog.list_models("opencode")
    assert _statuses(result) == {"opencode models": "unavailable", "config": "ok"}
    assert [m["id"] for m in result["models"]] == ["opencode/custom"]
