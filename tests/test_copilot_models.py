"""Leitura do seletor /model do Copilot e gravação do cache, sem o Copilot real.

O ``copilot`` falso é um script Python num diretório temporário que imprime uma tela de
seletor com o formato real (grupos, linhas ``Nome [✓] [(default)] Tier Contexto Esforço``).
"""

import json
import os
import stat
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from meister import copilot_models
from meister.cli import main
from meister.copilot_models import (
    CopilotModelsError,
    ParsedSelector,
    answer_terminal_queries,
    catalog_path,
    model_id_from_name,
    parse_selector,
    refresh_copilot_catalog,
    strip_ansi,
    write_catalog,
)
from tests.platform_marks import posix_only

SELECTOR_SCREEN = """\x1b[2J\x1b[1;1H\x1b[1mSelect a model\x1b[0m

Recent models
❯ GPT-6 Luna ✓ Lightweight 272K Medium
  Claude Haiku 5.5 Lightweight 100K Medium

Recommended models
  GPT-5.6 Terra (default) Versatile 272K Medium
  Claude Sonnet 5.5 Versatile 200K High

Other models
  GPT-5.4 Versatile 272K —
  Gemini 3.8 Flash Versatile 1M Medium

Unavailable models
  GPT-6 Sol Versatile 272K —

Local providers (Ollama)
  gpt-oss:20b
  llama3.1:latest

Enter to select · Esc to close
"""

EMPTY_SCREEN = "Select a model\n\nRecent models\n\nOther models\n\nEsc to close\n"

FAKE_COPILOT = """\
import sys

def out(text):
    sys.stdout.write(text)
    sys.stdout.flush()

if __SCREEN__:
    out("Do you trust the files in this folder?\\n> 1. Yes\\n  2. Yes, and remember\\n  3. No\\n")
    sys.stdin.readline()
    out("\\n> ")
    sys.stdin.readline()
    out(__SCREEN__)
sys.stdin.read()
"""


@pytest.fixture(autouse=True)
def fast_timings(monkeypatch):
    monkeypatch.setattr(copilot_models, "SETTLE_SECONDS", 0.3)
    monkeypatch.setattr(copilot_models, "TIMEOUT_SECONDS", 20.0)
    monkeypatch.setattr(copilot_models, "EXIT_GRACE_SECONDS", 0.5)


@pytest.fixture
def home(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    return home_dir


@pytest.fixture
def fake_copilot(tmp_path, monkeypatch):
    """Instala um ``copilot`` falso como único executável do PATH; ``screen`` vazio = seletor nunca aparece."""
    bin_dir = tmp_path / "bin"

    def install(screen: str) -> Path:
        bin_dir.mkdir(exist_ok=True)
        script = bin_dir / "copilot"
        script.write_text(f"#!{sys.executable}\n" + FAKE_COPILOT.replace("__SCREEN__", repr(screen)), encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        monkeypatch.setenv("PATH", str(bin_dir))
        return script

    return install


def _catalog(home: Path) -> Path:
    return home / ".meister" / "model_catalog" / "copilot.json"


@posix_only
def test_reads_full_selector_and_writes_catalog(home, fake_copilot):
    fake_copilot(SELECTOR_SCREEN)

    count, path = refresh_copilot_catalog()

    assert count == 6
    assert path == str(_catalog(home))
    data = json.loads(_catalog(home).read_text(encoding="utf-8"))
    assert [model["id"] for model in data["models"]] == [
        "gpt-6-luna",
        "claude-haiku-5.5",
        "gpt-5.6-terra",
        "claude-sonnet-5.5",
        "gpt-5.4",
        "gemini-3.8-flash",
    ]
    assert data["unavailable"] == ["gpt-6-sol"]
    assert data["local_ollama"] == ["gpt-oss:20b", "llama3.1:latest"]
    by_id = {model["id"]: model for model in data["models"]}
    assert by_id["gpt-6-luna"] == {
        "name": "GPT-6 Luna",
        "group": "recent",
        "tier": "Lightweight",
        "context": "272K",
        "reasoning": "Medium",
        "selected": True,
        "default": False,
        "id": "gpt-6-luna",
        "id_verified": False,
    }
    assert by_id["gpt-5.6-terra"]["default"] is True
    assert by_id["gpt-5.4"]["reasoning"] is None
    assert by_id["gemini-3.8-flash"]["context"] == "1M"
    assert by_id["gemini-3.8-flash"]["group"] == "other"
    assert "source" in data and "captured_at" in data and "note" in data


@posix_only
def test_empty_selector_does_not_overwrite_previous_catalog(home, fake_copilot):
    _catalog(home).parent.mkdir(parents=True)
    _catalog(home).write_text("OLD", encoding="utf-8")
    fake_copilot(EMPTY_SCREEN)

    with pytest.raises(CopilotModelsError) as error:
        refresh_copilot_catalog()

    assert error.value.key == "cli.config.refresh_models.no_models"
    assert _catalog(home).read_text(encoding="utf-8") == "OLD"
    assert not (_catalog(home).parent / "copilot.json.bak").exists()


@posix_only
def test_selector_that_never_appears_times_out(home, fake_copilot, monkeypatch):
    monkeypatch.setattr(copilot_models, "TIMEOUT_SECONDS", 2.0)
    fake_copilot("")

    with pytest.raises(CopilotModelsError) as error:
        refresh_copilot_catalog()

    assert error.value.key == "cli.config.refresh_models.timeout"
    assert error.value.fields == {"seconds": 2}
    assert not _catalog(home).exists()


def test_missing_copilot_in_path_exits_with_clear_error(home, tmp_path, monkeypatch):
    empty_bin = tmp_path / "empty"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    with pytest.raises(CopilotModelsError) as error:
        refresh_copilot_catalog()

    assert error.value.key == "cli.config.refresh_models.no_copilot"
    assert not _catalog(home).exists()


def test_non_unix_platform_exits_with_clear_error(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")

    with pytest.raises(CopilotModelsError) as error:
        refresh_copilot_catalog()

    assert error.value.key == "cli.config.refresh_models.unsupported_platform"


def test_write_is_atomic_and_keeps_previous_file_as_bak(home):
    catalog = _catalog(home)
    catalog.parent.mkdir(parents=True)
    catalog.write_text("OLD", encoding="utf-8")
    parsed = parse_selector(SELECTOR_SCREEN)

    write_catalog(parsed, str(catalog), "2026-10-18T10:00:00-03:00")

    assert catalog.parent.joinpath("copilot.json.bak").read_text(encoding="utf-8") == "OLD"
    assert json.loads(catalog.read_text(encoding="utf-8"))["captured_at"] == "2026-10-18T10:00:00-03:00"
    assert sorted(p.name for p in catalog.parent.iterdir()) == ["copilot.json", "copilot.json.bak"]


def test_failed_replace_keeps_previous_catalog_intact(home, monkeypatch):
    catalog = _catalog(home)
    catalog.parent.mkdir(parents=True)
    catalog.write_text("OLD", encoding="utf-8")
    real_replace = os.replace

    def failing_replace(source, target):
        if os.fspath(target) == str(catalog):
            raise OSError("disk full")
        return real_replace(source, target)

    monkeypatch.setattr(copilot_models.os, "replace", failing_replace)

    with pytest.raises(OSError):
        write_catalog(parse_selector(SELECTOR_SCREEN), str(catalog), "2026-10-18T10:00:00-03:00")

    assert catalog.read_text(encoding="utf-8") == "OLD"
    assert sorted(p.name for p in catalog.parent.iterdir()) == ["copilot.json", "copilot.json.bak"]


def test_catalog_path_respects_home(home):
    assert catalog_path() == str(_catalog(home))


def test_ids_are_lowercase_with_spaces_turned_into_hyphens():
    assert model_id_from_name("Claude Haiku 5.5") == "claude-haiku-5.5"
    assert model_id_from_name("GPT-5.3-Codex") == "gpt-5.3-codex"
    assert model_id_from_name("Gemini   3.8 Flash") == "gemini-3.8-flash"


def test_parse_selector_reads_groups_flags_and_absent_values():
    parsed = parse_selector(SELECTOR_SCREEN)

    assert isinstance(parsed, ParsedSelector)
    assert [model["group"] for model in parsed.models] == ["recent", "recent", "recommended", "recommended", "other", "other"]
    luna, _haiku, terra, *_rest = parsed.models
    assert luna["selected"] is True and luna["default"] is False
    assert terra["selected"] is False and terra["default"] is True
    assert all(model["id_verified"] is False for model in parsed.models)


def test_parse_selector_ignores_local_block_except_ollama_ids():
    parsed = parse_selector("Other models\nGPT-5.4 Versatile 272K Medium\nOllama (local)\n  gpt-oss:20b  running\nSome hint\n")

    assert [model["id"] for model in parsed.models] == ["gpt-5.4"]
    assert parsed.local_ollama == ["gpt-oss:20b"]


def test_parse_selector_without_groups_gives_nothing():
    parsed = parse_selector("GPT-5.4 Versatile 272K Medium\nEsc to close\n")

    assert parsed.models == []


def test_strip_ansi_removes_control_sequences_and_keeps_spacing():
    raw = "Claude\x1b[1CHaiku\x1b[2K\x1b[1A\x1b]0;title\x07 done\x1b[?996n"

    assert strip_ansi(raw) == "Claude Haiku done"


def test_terminal_queries_get_dark_theme_answers():
    buffer = b"\x1b]10;?\x07\x1b]11;?\x1b\\\x1b[?996n\x1b[?2026$p\x1b]4;3;?\x07"

    replies, rest = answer_terminal_queries(buffer)

    assert replies == (
        b"\x1b]10;rgb:ffff/ffff/ffff\x1b\\"
        b"\x1b]11;rgb:0000/0000/0000\x1b\\"
        b"\x1b[?997;1n"
        b"\x1b[?2026;2$y"
        b"\x1b]4;3;rgb:8080/8080/8080\x1b\\"
    )
    assert rest == b""


def test_incomplete_query_is_kept_for_the_next_read():
    replies, rest = answer_terminal_queries(b"screen text\x1b]11;?")

    assert replies == b""
    assert rest.endswith(b"\x1b]11;?")


@posix_only
def test_cli_refresh_models_copilot_writes_catalog(home, fake_copilot):
    fake_copilot(SELECTOR_SCREEN)

    result = CliRunner().invoke(main, ["config", "refresh-models", "copilot"])

    assert result.exit_code == 0, result.output
    assert "6" in result.output
    assert str(_catalog(home)) in result.output
    assert len(json.loads(_catalog(home).read_text(encoding="utf-8"))["models"]) == 6


@posix_only
def test_cli_refresh_models_copilot_exits_1_on_empty_selector(home, fake_copilot):
    fake_copilot(EMPTY_SCREEN)

    result = CliRunner().invoke(main, ["config", "refresh-models", "copilot"])

    assert result.exit_code == 1
    assert not _catalog(home).exists()


def test_refresh_models_only_accepts_known_harness():
    result = CliRunner().invoke(main, ["config", "refresh-models", "gemini"])

    assert result.exit_code == 2


def test_refresh_models_appears_in_config_help():
    result = CliRunner().invoke(main, ["config", "--help"])

    assert result.exit_code == 0
    assert "refresh-models" in result.output


def test_write_catalog_creates_missing_directory(home):
    catalog = _catalog(home)

    write_catalog(parse_selector(SELECTOR_SCREEN), str(catalog), "2026-10-18T10:00:00-03:00")

    assert json.loads(catalog.read_text(encoding="utf-8"))["unavailable"] == ["gpt-6-sol"]
    assert not catalog.parent.joinpath("copilot.json.bak").exists()


REAL_SELECTOR_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "copilot_model_selector.raw"
REAL_MODEL_NAMES = [
    "Claude Haiku 5.5",
    "GPT-6 Luna",
    "GPT-5.6 Terra",
    "Claude Sonnet 5.5",
    "GPT-5.6 Luna",
    "GPT-5.4",
    "GPT-5.4 mini",
    "GPT-5.3-Codex",
    "GPT-5 mini",
    "Claude Sonnet 5",
    "Claude Haiku 4.5",
    "MAI-Code-1.1-Flash",
    "Gemini 3.8 Flash",
    "Gemini 3.7 Flash",
    "Grok 4.7",
    "Grok 4.6",
    "Grok 4.5",
    "Kimi K3",
]


@pytest.fixture
def real_selector():
    """Saída bruta real do seletor ``/model`` do Copilot, já sem ANSI."""
    raw = REAL_SELECTOR_FIXTURE.read_bytes().decode("utf-8", errors="replace")
    return parse_selector(strip_ansi(raw))


def test_real_selector_lists_the_18_models_in_screen_order(real_selector):
    assert [model["name"] for model in real_selector.models] == REAL_MODEL_NAMES


def test_real_selector_marks_selected_and_default(real_selector):
    by_name = {model["name"]: model for model in real_selector.models}

    assert by_name["GPT-6 Luna"]["selected"] is True
    assert by_name["GPT-6 Luna"]["default"] is False
    assert by_name["GPT-5.6 Terra"]["default"] is True
    assert by_name["GPT-5.6 Terra"]["selected"] is False
    assert sum(model["selected"] for model in real_selector.models) == 1
    assert sum(model["default"] for model in real_selector.models) == 1


def test_real_selector_groups_have_expected_sizes(real_selector):
    groups = [model["group"] for model in real_selector.models]

    assert groups.count("recent") == 2
    assert groups.count("recommended") == 2
    assert groups.count("other") == 14


def test_real_selector_reads_tier_context_and_effort_glued_to_name(real_selector):
    by_name = {model["name"]: model for model in real_selector.models}

    assert by_name["Claude Haiku 5.5"]["tier"] == "Lightweight"
    assert by_name["Claude Haiku 5.5"]["context"] == "100K"
    assert by_name["Gemini 3.8 Flash"]["context"] == "200K"
    assert by_name["Kimi K3"]["tier"] == "Powerful"
    assert by_name["Kimi K3"]["reasoning"] == "High"


def test_real_selector_absent_context_and_effort_become_none(real_selector):
    by_name = {model["name"]: model for model in real_selector.models}

    assert by_name["GPT-5.4 mini"]["context"] is None
    assert by_name["GPT-5.4 mini"]["reasoning"] == "Medium"
    assert by_name["Claude Haiku 4.5"]["context"] is None
    assert by_name["Claude Haiku 4.5"]["reasoning"] is None


def test_real_selector_unavailable_and_local_blocks(real_selector):
    assert real_selector.unavailable == ["gpt-6.1-sol", "gpt-6-sol", "gpt-6-astra", "gpt-5.6-sol"]
    assert real_selector.local_ollama == ["gpt-oss:20b", "llama3.1:latest"]


def test_real_selector_ignores_help_text_and_chrome(real_selector):
    names = [model["name"] for model in real_selector.models]

    assert not any("Auto" in name or "Optimize" in name or "Category" in name for name in names)
    assert all(model["id_verified"] is False for model in real_selector.models)


@pytest.mark.parametrize(
    ("name", "expected_id"),
    [
        ("Claude Haiku 5.5", "claude-haiku-5.5"),
        ("GPT-5.4 mini", "gpt-5.4-mini"),
        ("MAI-Code-1.1-Flash", "mai-code-1.1-flash"),
        ("GPT-5.3-Codex", "gpt-5.3-codex"),
        ("Kimi K3", "kimi-k3"),
    ],
)
def test_real_selector_ids_come_from_names(real_selector, name, expected_id):
    by_name = {model["name"]: model for model in real_selector.models}

    assert by_name[name]["id"] == model_id_from_name(name) == expected_id
