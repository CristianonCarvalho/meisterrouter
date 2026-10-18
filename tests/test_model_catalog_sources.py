import json
import subprocess
from unittest import mock

import pytest

from meister import model_catalog


AGY_OUTPUT = (
    "gemini-4-argon-medium\tGemini 4 Argon (Medium)\n"
    "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
    "claude-sonnet-5-5-high\tClaude Sonnet 5.5 (High)\n"
    "gpt-oss-120b-medium\tGPT-OSS 120B (Medium)\n"
)


@pytest.fixture(autouse=True)
def isolated_sources(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    # USERPROFILE é a pasta pessoal no Windows (os.path.expanduser o usa lá).
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("MEISTER_CONFIG_PATH", raising=False)
    monkeypatch.chdir(project)
    model_catalog.clear_model_catalog_cache()
    yield home
    model_catalog.clear_model_catalog_cache()


@pytest.fixture
def agy_on_path(monkeypatch):
    monkeypatch.setattr(model_catalog.shutil, "which", lambda name: f"/fake/bin/{name}")


def _fake_agy(stdout="", returncode=0):
    return mock.Mock(return_value=subprocess.CompletedProcess(["agy", "models"], returncode, stdout, ""))


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


# --- agy models ---------------------------------------------------------------


def test_agy_models_output_gives_id_label_and_default_cost(agy_on_path):
    with mock.patch.object(model_catalog.subprocess, "run", _fake_agy(AGY_OUTPUT)) as run:
        result = model_catalog.list_models("agy")

    assert run.call_args.args[0] == ["agy", "models"]
    assert run.call_args.kwargs["timeout"] == 10
    assert "shell" not in run.call_args.kwargs
    assert result["sources"] == [{"name": "agy models", "status": "ok"}]
    assert [model["id"] for model in result["models"]] == [
        "gemini-4-argon-medium",
        "gemini-3.8-flash-high",
        "claude-sonnet-5-5-high",
        "gpt-oss-120b-medium",
    ]
    models = _by_id(result)
    assert models["gemini-3.8-flash-high"]["label"] == "Gemini 3.8 Flash (High)"
    assert models["gemini-3.8-flash-high"]["source"] == "agy models"
    assert models["gemini-3.8-flash-high"]["cost_per_m_tokens"] == 1.5
    assert models["gemini-4-argon-medium"]["cost_per_m_tokens"] is None


def test_agy_warning_line_is_discarded(agy_on_path):
    stdout = "Fetching available models...\n" + AGY_OUTPUT

    with mock.patch.object(model_catalog.subprocess, "run", _fake_agy(stdout)):
        result = model_catalog.list_models("agy")

    assert "Fetching available models..." not in [model["label"] for model in result["models"]]
    assert len(result["models"]) == 4


def test_agy_output_with_only_warning_is_empty_and_config_fills_in(agy_on_path):
    with mock.patch.object(model_catalog.subprocess, "run", _fake_agy("Fetching available models...\n")):
        result = model_catalog.list_models("agy")

    assert _statuses(result) == {"agy models": "empty", "config": "ok"}
    assert all(model["source"] == "config" for model in result["models"])


def test_agy_missing_executable_is_unavailable_and_never_runs(monkeypatch):
    monkeypatch.setattr(model_catalog.shutil, "which", lambda name: None)

    with mock.patch.object(model_catalog.subprocess, "run") as run:
        result = model_catalog.list_models("agy")

    assert not run.called
    assert _statuses(result) == {"agy models": "unavailable", "config": "ok"}


def test_agy_timeout_is_unavailable_and_config_fills_in(agy_on_path):
    run = mock.Mock(side_effect=subprocess.TimeoutExpired(["agy", "models"], 10))

    with mock.patch.object(model_catalog.subprocess, "run", run):
        result = model_catalog.list_models("agy")

    assert _statuses(result) == {"agy models": "unavailable", "config": "ok"}
    assert "gemini-3.8-flash-high" in _by_id(result)


def test_agy_nonzero_exit_is_unavailable(agy_on_path):
    with mock.patch.object(model_catalog.subprocess, "run", _fake_agy(AGY_OUTPUT, returncode=1)):
        result = model_catalog.list_models("agy")

    assert _statuses(result) == {"agy models": "unavailable", "config": "ok"}
    assert all(model["source"] == "config" for model in result["models"])


def test_agy_oserror_is_unavailable(agy_on_path):
    run = mock.Mock(side_effect=OSError("permissao negada"))

    with mock.patch.object(model_catalog.subprocess, "run", run):
        result = model_catalog.list_models("antigravity")

    assert _statuses(result) == {"agy models": "unavailable", "config": "ok"}


def test_agy_failure_does_not_affect_codex_source(agy_on_path, isolated_sources):
    _write_codex_cache(isolated_sources, {"models": [_codex_item("gpt-6-luna", "GPT-6-Luna")]})
    run = mock.Mock(side_effect=subprocess.TimeoutExpired(["agy", "models"], 10))

    with mock.patch.object(model_catalog.subprocess, "run", run):
        agy = model_catalog.list_models("agy")
    codex = model_catalog.list_models("codex")

    assert _statuses(agy) == {"agy models": "unavailable", "config": "ok"}
    assert _statuses(codex) == {"codex cache": "ok"}
    assert [model["id"] for model in codex["models"]] == ["gpt-6-luna"]


# --- codex cache --------------------------------------------------------------


def test_codex_cache_lists_only_visible_models(isolated_sources):
    _write_codex_cache(
        isolated_sources,
        {
            "models": [
                _codex_item("gpt-6-luna", "GPT-6-Luna"),
                _codex_item("gpt-reserve", "GPT-Reserve", visibility="hide"),
                _codex_item("gpt-5.6-terra", "GPT-5.6-Terra"),
                _codex_item("codex-auto-review", "Codex Auto Review", visibility="hide"),
            ]
        },
    )

    result = model_catalog.list_models("codex")

    assert [model["id"] for model in result["models"]] == ["gpt-6-luna", "gpt-5.6-terra"]
    assert result["models"][0]["label"] == "GPT-6-Luna"
    assert result["models"][0]["source"] == "codex cache"
    assert result["models"][0]["cost_per_m_tokens"] == 0.2
    assert result["models"][1]["cost_per_m_tokens"] is None
    assert result["sources"] == [{"name": "codex cache", "status": "ok"}]


def test_codex_cache_with_only_hidden_models_is_empty_and_config_fills_in(isolated_sources):
    _write_codex_cache(isolated_sources, {"models": [_codex_item("gpt-reserve", "GPT-Reserve", "hide")]})

    result = model_catalog.list_models("codex")

    assert _statuses(result) == {"codex cache": "empty", "config": "ok"}
    assert all(model["source"] == "config" for model in result["models"])


def test_codex_cache_missing_file_is_unavailable_and_config_fills_in():
    result = model_catalog.list_models("codex")

    assert _statuses(result) == {"codex cache": "unavailable", "config": "ok"}


@pytest.mark.parametrize(
    "content",
    ["{nao e json", "", '["lista solta"]', {"sem_models": []}, {"models": "nao e lista"}],
)
def test_codex_corrupted_or_unexpected_file_is_unavailable(isolated_sources, content):
    _write_codex_cache(isolated_sources, content)

    result = model_catalog.list_models("codex")

    assert _statuses(result) == {"codex cache": "unavailable", "config": "ok"}


def test_codex_cache_skips_items_without_usable_slug(isolated_sources):
    _write_codex_cache(
        isolated_sources,
        {
            "models": [
                {"display_name": "Sem slug", "visibility": "list"},
                {"slug": "  ", "display_name": "Slug vazio", "visibility": "list"},
                "linha solta",
                _codex_item("gpt-6-luna", "  "),
            ]
        },
    )

    result = model_catalog.list_models("codex")

    assert [model["id"] for model in result["models"]] == ["gpt-6-luna"]
    assert result["models"][0]["label"] is None


def test_codex_cache_is_read_from_home_only(isolated_sources):
    _write_codex_cache(isolated_sources, {"models": [_codex_item("do-home-temporario", "Temp")]})

    with mock.patch.object(model_catalog.subprocess, "run") as run:
        result = model_catalog.list_models("codex")

    assert not run.called
    assert [model["id"] for model in result["models"]] == ["do-home-temporario"]
