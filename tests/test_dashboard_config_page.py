from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import meister.dashboard.server as dashboard_server
from meister.dashboard.server import _config_messages, app
from meister.i18n import CATALOGS, reset_language_cache


NEW_CHAIN_KEYS = (
    "path_title", "path_help", "path_class", "path_start", "path_clear", "path_disabled", "path_before_start",
    "path_failed", "path_runs_here", "result_no_eligible", "result_all_failed", "result_ok",
    "result_failed_before", "result_escalated", "level_title", "level_other", "level_order_hint", "escalation",
    "escalation_cost", "chip_enabled", "chip_disabled", "chip_retries", "chip_all_classes", "chip_only_classes",
    "chip_parallel", "content_effort", "cost_value", "no_model", "legend_lateral", "legend_escalation",
)

DRAWER_KEYS = (
    "edit",
    "drawer_title",
    "close",
    "group_content",
    "group_policy",
    "rename_warning",
    "field_cost_per_m_tokens",
    "field_credit_usd",
    "field_max_retries",
    "field_idle_timeout",
    "field_max_runtime",
    "field_eligible_classes",
    "eligible_classes_all",
    "field_best_for",
    "remove_lane",
    "remove_lane_confirm",
    "remove_lane_yes",
    "err_invalid_name",
    "err_duplicate_name",
    "err_level_order",
    "err_empty_model",
    "err_invalid_number",
    "err_required",
    "err_no_enabled_lane",
)



@pytest.fixture
def page_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("MEISTER_LOG_DIR", str(log_dir))
    monkeypatch.setattr(dashboard_server, "_has_active_run", lambda: False)
    yield app.test_client()
    reset_language_cache()


@pytest.mark.parametrize("language", ["en", "pt-BR"])
def test_config_page_renders_translated_texts_and_links(page_client, monkeypatch, language):
    monkeypatch.setenv("MEISTER_LANG", language)
    reset_language_cache()
    resp = page_client.get("/config")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'href="/timeline"' in html
    assert 'href="/"' in html
    assert "/api/config" in html
    catalog = CATALOGS[language]
    for key in ("title", "back_timeline", "dashboard", "blocked_run_active", "read_only_remote"):
        assert catalog[f"cli.config.web.{key}"] in html


def test_config_messages_keys_exist_in_both_catalogs():
    messages = _config_messages()
    assert messages
    for key in messages:
        assert f"cli.config.web.{key}" in CATALOGS["en"]
        assert f"cli.config.web.{key}" in CATALOGS["pt-BR"]


def test_config_page_writes_with_csrf_header(page_client):
    html = page_client.get("/config").get_data(as_text=True)
    assert "/api/config/lanes" in html
    assert "/api/config/restore-default" in html
    assert '"X-Meister-Config": "1"' in html
    assert '"Content-Type": "application/json"' in html


def test_timeline_links_to_config(page_client):
    html = page_client.get("/timeline").get_data(as_text=True)
    assert 'href="/config"' in html


def _page_texts(html: str) -> dict:
    match = re.search(r"const text = (\{.*?\});\n", html)
    assert match
    return json.loads(match.group(1))


@pytest.mark.parametrize("language", ["en", "pt-BR"])
def test_config_page_embeds_chain_texts(page_client, monkeypatch, language):
    monkeypatch.setenv("MEISTER_LANG", language)
    reset_language_cache()
    html = page_client.get("/config").get_data(as_text=True)
    texts = _page_texts(html)
    catalog = CATALOGS[language]
    for key in NEW_CHAIN_KEYS:
        assert texts[key] == catalog[f"cli.config.web.{key}"]
        assert texts[key]
    assert html.count("// helpers:begin") == 1
    assert html.count("// helpers:end") == 1
    assert html.count("function simulatePath(") == 1
    assert html.count("function levelCostSteps(") == 1


def test_config_page_texts_cover_every_key_the_page_uses(page_client):
    html = page_client.get("/config").get_data(as_text=True)
    texts = _page_texts(html)
    used = set(re.findall(r"\btext\.(\w+)", html)) | set(re.findall(r"\{\{ messages\.(\w+) \}\}", html))
    assert used
    assert used <= set(texts)
    assert all(texts[key] for key in used)


@pytest.mark.parametrize("language", ["en", "pt-BR"])
def test_config_page_embeds_drawer_texts(page_client, monkeypatch, language):
    monkeypatch.setenv("MEISTER_LANG", language)
    reset_language_cache()
    html = page_client.get("/config").get_data(as_text=True)
    texts = _page_texts(html)
    catalog = CATALOGS[language]
    for key in DRAWER_KEYS:
        assert texts[key] == catalog[f"cli.config.web.{key}"]
        assert texts[key]


def test_config_page_has_drawer_markup(page_client):
    html = page_client.get("/config").get_data(as_text=True)
    assert 'id="drawer"' in html
    assert 'id="drawer-backdrop"' in html
    assert 'id="drawer-remove"' in html
    assert 'id="drawer-remove-confirm"' in html
    assert 'id="drawer-harness"' in html
    assert 'id="drawer-effort"' in html
    assert 'id="drawer-model"' in html
    assert 'id="drawer-cost-per-m-tokens"' in html
    assert 'id="drawer-credit-usd"' in html
    assert 'id="drawer-name"' in html
    assert 'id="drawer-enabled"' in html
    assert 'id="drawer-max-retries"' in html
    assert 'id="drawer-max-parallel"' in html
    assert 'id="drawer-idle-timeout-seconds"' in html
    assert 'id="drawer-max-runtime-seconds"' in html
    assert 'id="drawer-classes"' in html
    assert 'id="drawer-best-for"' in html
    assert "function clientFieldErrors(" in html
    assert "function openDrawer(" in html
    assert "function closeDrawer(" in html

