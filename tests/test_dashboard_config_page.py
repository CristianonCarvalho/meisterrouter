from __future__ import annotations

from pathlib import Path

import pytest

import meister.dashboard.server as dashboard_server
from meister.dashboard.server import _config_messages, app
from meister.i18n import CATALOGS, reset_language_cache


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
