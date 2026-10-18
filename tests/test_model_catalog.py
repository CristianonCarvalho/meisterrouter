import json
import subprocess
from unittest import mock

import pytest

from meister import model_catalog


@pytest.fixture(autouse=True)
def isolated_catalog(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("MEISTER_CONFIG_PATH", raising=False)
    monkeypatch.chdir(project)
    # Sem agy no PATH: a fonte `agy models` nunca roda o executável real.
    monkeypatch.setattr(model_catalog.shutil, "which", lambda name: None)
    model_catalog.clear_model_catalog_cache()
    yield home
    model_catalog.clear_model_catalog_cache()


def _write_copilot(home, content):
    path = home / ".meister" / "model_catalog"
    path.mkdir(parents=True, exist_ok=True)
    text = content if isinstance(content, str) else json.dumps(content)
    (path / "copilot.json").write_text(text, encoding="utf-8")


def _by_id(result):
    return {model["id"]: model for model in result["models"]}


def _statuses(result):
    return {source["name"]: source["status"] for source in result["sources"]}


def test_claude_aliases_and_full_ids_are_listed():
    result = model_catalog.list_models("claude")

    assert result["harness"] == "claude"
    assert [model["id"] for model in result["models"]] == list(model_catalog._CLAUDE_ALIASES)
    assert all(model["source"] == "claude aliases" for model in result["models"])
    assert all(model["label"] is None for model in result["models"])
    assert result["sources"] == [{"name": "claude aliases", "status": "ok"}]


def test_claude_cost_comes_from_default_catalog_when_id_matches():
    models = _by_id(model_catalog.list_models("claude"))

    assert models["sonnet"]["cost_per_m_tokens"] == 4.0
    assert models["opus"]["cost_per_m_tokens"] == 8.0
    assert models["claude-sonnet-5-5"]["cost_per_m_tokens"] is None


def test_copilot_cache_gives_ids_and_labels(isolated_catalog):
    _write_copilot(
        isolated_catalog,
        {
            "models": [
                {"name": "Claude Haiku 5.5", "id": "claude-haiku-5.5"},
                {"name": "GPT-6 Luna", "id": "gpt-6-luna"},
                {"name": "Sem id"},
                "linha solta",
                {"id": "  "},
            ]
        },
    )

    result = model_catalog.list_models("copilot")

    assert [model["id"] for model in result["models"]] == ["claude-haiku-5.5", "gpt-6-luna"]
    assert result["models"][0]["label"] == "Claude Haiku 5.5"
    assert result["models"][0]["source"] == "copilot cache"
    assert result["models"][0]["cost_per_m_tokens"] == 0.2
    assert result["models"][1]["cost_per_m_tokens"] == 0.2
    assert result["sources"] == [{"name": "copilot cache", "status": "ok"}]


def test_copilot_missing_file_is_unavailable_and_config_fills_in():
    result = model_catalog.list_models("copilot")

    assert _statuses(result) == {"copilot cache": "unavailable", "config": "ok"}
    assert "claude-haiku-5.5" in _by_id(result)
    assert all(model["source"] == "config" for model in result["models"])


@pytest.mark.parametrize("content", ["{nao e json", "[1, 2, 3]", {"models": "nao e lista"}])
def test_copilot_corrupted_file_is_unavailable(isolated_catalog, content):
    _write_copilot(isolated_catalog, content)

    result = model_catalog.list_models("copilot")

    assert _statuses(result) == {"copilot cache": "unavailable", "config": "ok"}


def test_copilot_empty_list_is_empty_and_config_fills_in(isolated_catalog):
    _write_copilot(isolated_catalog, {"models": [{"name": "Sem id"}]})

    result = model_catalog.list_models("copilot")

    assert _statuses(result) == {"copilot cache": "empty", "config": "ok"}


def test_copilot_cache_wins_over_config_for_same_id(isolated_catalog):
    _write_copilot(isolated_catalog, {"models": [{"name": "Haiku da tela", "id": "claude-haiku-5.5"}]})

    models = _by_id(model_catalog.list_models("copilot"))

    assert models["claude-haiku-5.5"]["source"] == "copilot cache"
    assert models["claude-haiku-5.5"]["label"] == "Haiku da tela"


def test_config_source_lists_default_models_for_agy_and_alias_matches():
    result = model_catalog.list_models("agy")
    models = _by_id(result)

    assert _statuses(result) == {"agy models": "unavailable", "config": "ok"}
    assert models["gemini-3.8-flash-high"]["cost_per_m_tokens"] == 1.5
    assert models["gemini-3.8-flash-high"]["source"] == "config"
    assert model_catalog.list_models("antigravity")["models"] == result["models"]


def test_config_source_includes_project_tiers(tmp_path):
    (tmp_path / "project" / "meister.config.yaml").write_text(
        "workers:\n"
        "  tier_order:\n"
        "    - {name: tier_x, harness: agy, model: modelo-do-projeto}\n",
        encoding="utf-8",
    )

    models = _by_id(model_catalog.list_models("agy"))

    assert models["modelo-do-projeto"]["cost_per_m_tokens"] is None
    assert models["gemini-3.8-flash-high"]["cost_per_m_tokens"] == 1.5


def test_broken_project_config_makes_config_source_unavailable(tmp_path):
    (tmp_path / "project" / "meister.config.yaml").write_text("workers: [\n", encoding="utf-8")

    result = model_catalog.list_models("agy")

    assert result == {
        "harness": "agy",
        "models": [],
        "sources": [{"name": "agy models", "status": "unavailable"}, {"name": "config", "status": "unavailable"}],
    }


def test_config_source_empty_for_harness_without_default_tiers():
    result = model_catalog.list_models("kiro-cli")

    assert result == {"harness": "kiro-cli", "models": [], "sources": [{"name": "config", "status": "empty"}]}


def test_cost_comes_from_default_when_codex_id_matches_a_default_tier():
    models = _by_id(model_catalog.list_models("codex"))

    assert models["gpt-6-luna"]["cost_per_m_tokens"] == 0.2


def test_harness_aliases_share_the_same_catalog():
    assert model_catalog.list_models("github-copilot")["models"] == model_catalog.list_models("copilot")["models"]
    assert model_catalog.list_models("kiro")["sources"] == model_catalog.list_models("kiro-cli")["sources"]


def test_unknown_harness_returns_empty_lists_without_error():
    assert model_catalog.list_models("nao-existe") == {"harness": "nao-existe", "models": [], "sources": []}


def test_cache_serves_first_read_until_cleared(isolated_catalog):
    _write_copilot(isolated_catalog, {"models": [{"id": "primeiro"}]})
    assert "primeiro" in _by_id(model_catalog.list_models("copilot"))

    _write_copilot(isolated_catalog, {"models": [{"id": "segundo"}]})
    assert "primeiro" in _by_id(model_catalog.list_models("copilot"))

    model_catalog.clear_model_catalog_cache()
    assert "segundo" in _by_id(model_catalog.list_models("copilot"))


def test_cache_expires_after_ttl(isolated_catalog, monkeypatch):
    monkeypatch.setattr(model_catalog, "CACHE_TTL_SECONDS", 0.0)
    _write_copilot(isolated_catalog, {"models": [{"id": "primeiro"}]})
    model_catalog.list_models("copilot")

    _write_copilot(isolated_catalog, {"models": [{"id": "segundo"}]})

    assert "segundo" in _by_id(model_catalog.list_models("copilot"))


def test_returned_lists_do_not_leak_into_cache():
    first = model_catalog.list_models("claude")
    first["models"][0]["id"] = "alterado"
    first["sources"].clear()

    second = model_catalog.list_models("claude")

    assert second["models"][0]["id"] == "haiku"
    assert second["sources"] == [{"name": "claude aliases", "status": "ok"}]


def test_no_subprocess_is_started():
    # mock.patch restaura no fim do bloco; o monkeypatch só desfaz depois do teardown das fixtures
    # do conftest (que também usam subprocess.run), então não serve aqui.
    with mock.patch.object(subprocess, "run") as run, mock.patch.object(subprocess, "Popen") as popen:
        for harness in ("claude", "copilot", "github-copilot", "agy", "antigravity", "codex", "kiro", "kiro-cli", "x"):
            model_catalog.list_models(harness)

    assert not run.called
    assert not popen.called
