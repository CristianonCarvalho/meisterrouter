from __future__ import annotations

import json

from tests.config_js import CONFIG_TEMPLATE, run_js


def _js(value: object) -> str:
    return json.dumps(value)


LANES = [{"name": "tier_1", "harness": "codex", "model": "gpt-6-luna", "cost_per_m_tokens": 0.2, "enabled": True}]

PROJECTS = [
    {"id": "aaa111222333", "name": "alpha", "path": "/x/alpha", "exists": True},
    {"id": "bbb111222333", "name": "beta", "path": "/x/beta", "exists": False},
]


def test_config_url_without_project_is_the_plain_endpoint() -> None:
    assert run_js("configUrl(null)") == "/api/config"
    assert run_js("configUrl('')") == "/api/config"


def test_config_url_carries_the_project_id() -> None:
    assert run_js("configUrl('aaa111222333')") == "/api/config?project=aaa111222333"


def test_config_url_encodes_the_project_id() -> None:
    assert run_js("configUrl('a b&c')") == "/api/config?project=a%20b%26c"


def test_with_project_adds_the_key_without_mutating_the_body() -> None:
    result = run_js(
        f"(() => {{ const body = {_js({'stamp': 's'})}; const out = withProject(body, 'aaa'); return {{ out, body }}; }})()"
    )
    assert result == {"out": {"stamp": "s", "project": "aaa"}, "body": {"stamp": "s"}}


def test_with_project_keeps_the_body_when_there_is_no_project() -> None:
    assert run_js("withProject({ stamp: 's' }, null)") == {"stamp": "s"}


def test_save_request_carries_the_project_in_the_body() -> None:
    request = run_js(f"saveRequest('s', {_js(LANES)}, 'aaa111222333')")
    body = json.loads(request["body"])
    assert body["project"] == "aaa111222333"
    assert body["stamp"] == "s"
    assert body["lanes"][0]["name"] == "tier_1"


def test_save_request_without_project_has_no_project_key() -> None:
    request = run_js(f"saveRequest('s', {_js(LANES)}, null)")
    assert "project" not in json.loads(request["body"])


def test_restore_request_carries_the_project_in_the_body() -> None:
    request = run_js("restoreRequest('s', 'aaa111222333')")
    assert json.loads(request["body"]) == {"stamp": "s", "project": "aaa111222333"}
    assert request["headers"] == {"Content-Type": "application/json", "X-Meister-Config": "1"}


def test_project_options_keep_name_path_hint_and_missing_flag() -> None:
    options = run_js(f"projectOptions({_js(PROJECTS)}, null)")
    assert options == [
        {"id": "aaa111222333", "name": "alpha", "path": "/x/alpha", "missing": False},
        {"id": "bbb111222333", "name": "beta", "path": "/x/beta", "missing": True},
    ]


def test_project_options_add_the_current_project_when_it_is_not_listed() -> None:
    current = {"id": "ccc111222333", "name": "gamma", "path": "/x/gamma"}
    options = run_js(f"projectOptions({_js(PROJECTS)}, {_js(current)})")
    assert len(options) == 3
    assert options[0] == {"id": "ccc111222333", "name": "gamma", "path": "/x/gamma", "missing": False}


def test_project_options_do_not_duplicate_a_listed_current_project() -> None:
    current = {"id": "aaa111222333", "name": "alpha", "path": "/x/alpha"}
    options = run_js(f"projectOptions({_js(PROJECTS)}, {_js(current)})")
    assert [option["id"] for option in options] == ["aaa111222333", "bbb111222333"]


def test_project_options_is_empty_without_projects() -> None:
    assert run_js("projectOptions(null, null)") == []


def test_project_options_fall_back_to_the_id_when_there_is_no_name() -> None:
    options = run_js("projectOptions([{ id: 'ddd', path: '' }], null)")
    assert options == [{"id": "ddd", "name": "ddd", "path": "", "missing": False}]


def test_save_outcome_treats_a_missing_project_as_an_error_not_a_stale_file() -> None:
    assert run_js("saveOutcome(409, { reason: 'project_missing' })") == "error"
    assert run_js("saveOutcome(409, { reason: 'unknown_project' })") == "error"
    assert run_js("saveOutcome(409, { reason: 'stale' })") == "stale"


def test_template_reads_and_writes_through_the_project_helpers() -> None:
    html = CONFIG_TEMPLATE.read_text(encoding="utf-8")
    assert "fetch(configUrl(requested))" in html
    assert 'fetch("/api/config")' not in html
    assert "saveRequest(state.stamp, state.lanes, state.projectId)" in html
    assert "restoreRequest(state.stamp, state.projectId)" in html


def test_template_loads_the_project_list_before_the_first_config_read() -> None:
    html = CONFIG_TEMPLATE.read_text(encoding="utf-8")
    assert 'fetch("/api/projects")' in html
    assert "loadProjects().then(load);" in html


def test_template_asks_before_switching_with_unsaved_changes() -> None:
    html = CONFIG_TEMPLATE.read_text(encoding="utf-8")
    assert 'id="project-confirm"' in html
    assert "if (lanesChanged(state.original, state.lanes)) {" in html
    assert "function renderProjectSwitch(" in html
    assert "state.pendingProjectId = next;" in html


def test_template_disables_the_project_select_while_a_write_is_busy() -> None:
    html = CONFIG_TEMPLATE.read_text(encoding="utf-8")
    assert 'byId("project-select").disabled = state.busy;' in html
