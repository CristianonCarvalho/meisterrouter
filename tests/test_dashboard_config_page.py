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

CHANGES_PANEL_KEYS = (
    "add_lane",
    "add_level",
    "changes_title",
    "changes_none",
    "change_added",
    "change_removed",
    "change_renamed",
    "change_fields",
    "change_reordered",
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
    match = re.search(r"const (?:rawText|text) = (\{.*?\});\n", html)
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


@pytest.mark.parametrize("language", ["en", "pt-BR"])
def test_config_page_embeds_task7_texts(page_client, monkeypatch, language):
    monkeypatch.setenv("MEISTER_LANG", language)
    reset_language_cache()
    html = page_client.get("/config").get_data(as_text=True)
    texts = _page_texts(html)
    catalog = CATALOGS[language]
    for key in CHANGES_PANEL_KEYS:
        assert texts[key] == catalog[f"cli.config.web.{key}"]
        assert texts[key]


def test_config_page_has_swap_and_creation_markup(page_client):
    html = page_client.get("/config").get_data(as_text=True)
    assert 'id="panel-changes"' in html
    assert 'id="changes-summary"' in html
    assert 'id="changes-list"' in html
    assert 'id="add-level"' in html
    assert "add-lane-btn" in html
    assert "function swapContent(" in html
    assert "function diffChain(" in html
    assert "function renderChanges(" in html
    assert "function isContentChanged(" in html


MODEL_TREE_KEYS = (
    "model_unknown",
    "tree_title",
    "tree_loading",
    "tree_unavailable",
    "tree_empty",
)


@pytest.mark.parametrize("language", ["en", "pt-BR"])
def test_config_page_embeds_model_tree_texts(page_client, monkeypatch, language):
    monkeypatch.setenv("MEISTER_LANG", language)
    reset_language_cache()
    html = page_client.get("/config").get_data(as_text=True)
    texts = _page_texts(html)
    catalog = CATALOGS[language]
    for key in MODEL_TREE_KEYS:
        assert texts[key] == catalog[f"cli.config.web.{key}"]
        assert texts[key]
    assert "modelo desconhecido" in CATALOGS["pt-BR"]["cli.config.web.model_unknown"]


def test_config_messages_include_model_tree_keys():
    messages = _config_messages()
    for key in MODEL_TREE_KEYS:
        assert key in messages


def test_config_page_has_model_tree_markup(page_client):
    html = page_client.get("/config").get_data(as_text=True)
    assert 'id="model-tree"' in html
    assert 'id="model-unknown"' in html
    assert "function modelKnown(" in html
    assert "function mergeCatalogChoice(" in html


def test_config_page_serves_no_new_fixed_text_outside_the_text_block(page_client, monkeypatch):
    monkeypatch.setenv("MEISTER_LANG", "pt-BR")
    reset_language_cache()
    html = page_client.get("/config").get_data(as_text=True)
    outside = re.sub(r"const (?:rawText|text) = \{.*?\};\n", "", html, count=1)
    catalog = CATALOGS["pt-BR"]
    for key in MODEL_TREE_KEYS:
        assert catalog[f"cli.config.web.{key}"] not in outside


def test_config_page_contains_older_server_warning_hidden_by_default(page_client):
    html = page_client.get("/config").get_data(as_text=True)
    assert "Restart the dashboard: the server is older than this page." in html
    assert "Reinicie o dashboard: o servidor é mais antigo que esta página." in html
    assert 'id="banner-server-older"' in html
    match = re.search(r'<div[^>]+id="banner-server-older"[^>]*>', html)
    assert match is not None
    assert "hidden" in match.group(0)


PROJECT_JS_KEYS = ("project_strip", "project_missing")
PROJECT_RENDERED_KEYS = ("project_label", "project_switch_confirm", "project_switch_yes")


@pytest.mark.parametrize("language", ["en", "pt-BR"])
def test_config_page_embeds_project_texts(page_client, monkeypatch, language):
    monkeypatch.setenv("MEISTER_LANG", language)
    reset_language_cache()
    html = page_client.get("/config").get_data(as_text=True)
    texts = _page_texts(html)
    catalog = CATALOGS[language]
    for key in PROJECT_JS_KEYS:
        assert texts[key] == catalog[f"cli.config.web.{key}"]
        assert texts[key]
    for key in PROJECT_RENDERED_KEYS:
        assert catalog[f"cli.config.web.{key}"] in html


def test_config_messages_include_project_keys():
    messages = _config_messages()
    for key in (*PROJECT_JS_KEYS, *PROJECT_RENDERED_KEYS):
        assert key in messages


def test_config_page_has_project_bar_markup(page_client):
    html = page_client.get("/config").get_data(as_text=True)
    assert 'id="project-select"' in html
    assert 'id="project-strip"' in html
    assert "function renderProjects(" in html
    assert "function switchProject(" in html
    assert "/api/projects" in html


def test_config_page_serves_no_new_fixed_project_text_outside_the_text_block(page_client, monkeypatch):
    monkeypatch.setenv("MEISTER_LANG", "pt-BR")
    reset_language_cache()
    html = page_client.get("/config").get_data(as_text=True)
    outside = re.sub(r"const (?:rawText|text) = \{.*?\};\n", "", html, count=1)
    catalog = CATALOGS["pt-BR"]
    for key in PROJECT_JS_KEYS:
        assert catalog[f"cli.config.web.{key}"] not in outside
    assert 'id="project-confirm"' in outside


PANEL_JS_KEYS = (
    "discover_found",
    "discover_none",
    "discover_registered",
    "discover_registered_count",
    "discover_scanning",
    "add_project_done",
    "project_request_failed",
)
PANEL_RENDERED_KEYS = (
    "discover_projects",
    "add_project",
    "discover_title",
    "discover_help",
    "discover_base",
    "discover_scan",
    "discover_register",
    "add_project_title",
    "add_project_path",
    "add_project_submit",
)


@pytest.mark.parametrize("language", ["en", "pt-BR"])
def test_config_page_embeds_project_panel_texts(page_client, monkeypatch, language):
    monkeypatch.setenv("MEISTER_LANG", language)
    reset_language_cache()
    html = page_client.get("/config").get_data(as_text=True)
    texts = _page_texts(html)
    catalog = CATALOGS[language]
    for key in PANEL_JS_KEYS:
        assert texts[key] == catalog[f"cli.config.web.{key}"]
        assert texts[key]
    for key in PANEL_RENDERED_KEYS:
        assert catalog[f"cli.config.web.{key}"] in html


def test_config_messages_include_project_panel_keys():
    messages = _config_messages()
    for key in (*PANEL_JS_KEYS, *PANEL_RENDERED_KEYS):
        assert key in messages


def test_config_page_has_project_panel_markup(page_client):
    html = page_client.get("/config").get_data(as_text=True)
    bar = re.search(r'class="project-bar">(.*?)id="project-confirm"', html, re.S)
    assert bar is not None
    assert 'id="discover-open"' in bar.group(1)
    assert 'id="add-open"' in bar.group(1)
    for marker in (
        'id="discover-panel"',
        'id="discover-base"',
        'id="discover-scan"',
        'id="discover-list"',
        'id="discover-register"',
        'id="err-discover-base"',
        'id="add-panel"',
        'id="add-path"',
        'id="add-submit"',
        'id="err-add-path"',
    ):
        assert marker in html
    for function in ("scanProjects(", "registerSelected(", "addProject(", "renderProjectPanels(", "selectedPaths("):
        assert f"function {function}" in html


def test_config_page_serves_no_new_fixed_panel_text_outside_the_text_block(page_client, monkeypatch):
    monkeypatch.setenv("MEISTER_LANG", "pt-BR")
    reset_language_cache()
    html = page_client.get("/config").get_data(as_text=True)
    outside = re.sub(r"const (?:rawText|text) = \{.*?\};\n", "", html, count=1)
    catalog = CATALOGS["pt-BR"]
    for key in PANEL_JS_KEYS:
        assert catalog[f"cli.config.web.{key}"] not in outside


REPLICATE_KEYS = (
    "save_all",
    "save_all_confirm",
    "save_all_result_title",
    "save_all_summary",
    "save_all_state_saved",
    "save_all_state_run_active",
    "save_all_state_missing",
    "save_all_state_failed",
    "save_all_yes",
)


def test_replicate_keys_exist_in_both_catalogs_with_the_same_keys():
    en_keys = {key for key in CATALOGS["en"] if key.startswith("cli.config.web.save_all")}
    pt_keys = {key for key in CATALOGS["pt-BR"] if key.startswith("cli.config.web.save_all")}
    expected = {f"cli.config.web.{key}" for key in REPLICATE_KEYS}
    assert en_keys == expected
    assert pt_keys == expected


def test_config_messages_include_replicate_keys():
    messages = _config_messages()
    for key in REPLICATE_KEYS:
        assert key in messages


@pytest.mark.parametrize("language", ["en", "pt-BR"])
def test_config_page_embeds_replicate_texts(page_client, monkeypatch, language):
    monkeypatch.setenv("MEISTER_LANG", language)
    reset_language_cache()
    html = page_client.get("/config").get_data(as_text=True)
    texts = _page_texts(html)
    catalog = CATALOGS[language]
    for key in REPLICATE_KEYS:
        assert texts[key] == catalog[f"cli.config.web.{key}"]
        assert texts[key]
    assert catalog["cli.config.web.save_all"] in html
    assert catalog["cli.config.web.save_all_result_title"] in html


def test_config_page_has_save_all_markup(page_client):
    html = page_client.get("/config").get_data(as_text=True)
    for marker in ('id="save-all"', 'id="save-all-confirm"', 'id="save-all-result"', 'id="save-all-list"'):
        assert marker in html
    bar = re.search(r'<button type="button" id="save"[^>]*>.*?<button type="button" id="save-all"', html, re.S)
    assert bar is not None
    assert "function replicateAll(" in html
    assert "function renderReplicateResult(" in html
    assert "/api/config/replicate" in html


def test_config_page_serves_no_new_replicate_text_outside_the_text_block(page_client, monkeypatch):
    monkeypatch.setenv("MEISTER_LANG", "pt-BR")
    reset_language_cache()
    html = page_client.get("/config").get_data(as_text=True)
    outside = re.sub(r"const (?:rawText|text) = \{.*?\};\n", "", html, count=1)
    catalog = CATALOGS["pt-BR"]
    for key in ("save_all_confirm", "save_all_summary", "save_all_state_run_active", "save_all_state_failed"):
        assert catalog[f"cli.config.web.{key}"] not in outside


@pytest.mark.parametrize("language", ["en", "pt-BR"])
def test_save_all_button_has_its_own_confirm_text(page_client, monkeypatch, language):
    monkeypatch.setenv("MEISTER_LANG", language)
    reset_language_cache()
    html = page_client.get("/config").get_data(as_text=True)
    catalog = CATALOGS[language]
    match = re.search(r'<button type="button" id="save-all-yes"[^>]*>(.*?)</button>', html, re.S)
    assert match is not None
    assert match.group(1) == catalog["cli.config.web.save_all_yes"]
    assert match.group(1) != catalog["cli.config.web.confirm_yes"]
    restore = re.search(r'<button type="button" id="restore-yes"[^>]*>(.*?)</button>', html, re.S)
    assert restore is not None
    assert restore.group(1) == catalog["cli.config.web.confirm_yes"]


def test_save_all_yes_is_in_config_messages():
    assert "save_all_yes" in _config_messages()


def _function_body(html: str, name: str) -> str:
    start = html.index(f"function {name}(")
    brace = html.index("{", start)
    depth = 0
    for position in range(brace, len(html)):
        if html[position] == "{":
            depth += 1
        elif html[position] == "}":
            depth -= 1
            if depth == 0:
                return html[brace:position + 1]
    raise AssertionError(f"unterminated function {name}")


def test_on_drawer_lane_changed_refreshes_changes_panel(page_client):
    html = page_client.get("/config").get_data(as_text=True)
    assert "renderChanges()" in _function_body(html, "onDrawerLaneChanged")


