from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from meister.dashboard.server import app
from meister.i18n import CATALOGS, reset_language_cache
from tests.timeline_js import TIMELINE_TEMPLATE

NODE = shutil.which("node")


@pytest.fixture
def timeline_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".meister").mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("MEISTER_LOG_DIR", str(log_dir))
    yield app.test_client()
    reset_language_cache()


@pytest.mark.parametrize("language", ["en", "pt-BR"])
def test_timeline_page_renders_project_select_and_translated_label(
    timeline_client, monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    monkeypatch.setenv("MEISTER_LANG", language)
    reset_language_cache()

    resp = timeline_client.get("/timeline")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'id="project-select"' in html
    assert '<label for="project-select">' in html

    label_text = CATALOGS[language]["cli.timeline.web.project_label"]
    assert label_text in html


def test_timeline_template_wires_with_project_for_runs_timelines_and_timeline() -> None:
    source = TIMELINE_TEMPLATE.read_text(encoding="utf-8")

    assert 'withProject("/api/runs"' in source
    assert 'withProject("/api/timelines' in source
    assert 'withProject("/api/timeline' in source


def test_timeline_template_has_project_wiring_helpers_and_handlers() -> None:
    source = TIMELINE_TEMPLATE.read_text(encoding="utf-8")

    assert "projectFromSearch(" in source
    assert "withProject(" in source
    assert "projectOptions(" in source
    assert "renderProjects(" in source
    assert "loadProjects(" in source
    assert "switchProject(" in source
    assert "handleProjectError(" in source
    assert 'fetch("/api/projects")' in source
    assert 'byId("project-select")' in source
    assert 'addEventListener("change"' in source


HARNESS_BOOT_JS = r"""
const fs = require("fs");
const vm = require("vm");

const [scriptPath, searchParam, errorMode] = process.argv.slice(2);
const script = fs.readFileSync(scriptPath, "utf8");
const errors = [];
const fetchCalls = [];
const historyCalls = [];

function makeElement() {
  const cache = {};
  const attrs = {};
  const listeners = {};
  const target = function () {};
  return new Proxy(target, {
    get(_t, prop) {
      if (typeof prop === "symbol" || prop === "then") return undefined;
      if (prop === "toString" || prop === "valueOf") return () => "";
      if (prop === "setAttribute") {
        return (name, value) => { attrs[name] = String(value); };
      }
      if (prop === "addEventListener") {
        return (event, handler) => {
          if (!listeners[event]) listeners[event] = [];
          listeners[event].push(handler);
        };
      }
      if (prop === "__attrs") return attrs;
      if (prop === "__listeners") return listeners;
      if (prop === "replaceChildren") {
        return (...children) => { cache.children = children; };
      }
      if (!(prop in cache)) cache[prop] = makeElement();
      return cache[prop];
    },
    set(_t, prop, value) {
      cache[prop] = value;
      return true;
    },
    apply() {
      return makeElement();
    },
  });
}

const elementsById = new Map();
const document = {
  visibilityState: "visible",
  title: "",
  documentElement: makeElement(),
  getElementById(id) {
    if (!elementsById.has(id)) elementsById.set(id, makeElement());
    return elementsById.get(id);
  },
  querySelector: () => makeElement(),
  querySelectorAll: () => [],
  createElement: () => makeElement(),
  createElementNS: () => makeElement(),
  addEventListener() {},
  removeEventListener() {},
};

const window = {
  location: { hash: "", pathname: "/timeline", search: searchParam || "" },
  history: {
    replaceState(_state, _title, url) {
      historyCalls.push(url);
    },
  },
  localStorage: { getItem: () => null, setItem: () => {} },
  matchMedia: () => ({ matches: false }),
  addEventListener() {},
  removeEventListener() {},
  innerWidth: 1280,
  innerHeight: 800,
  AudioContext: undefined,
  webkitAudioContext: undefined,
};

globalThis.window = window;
globalThis.document = document;
globalThis.HTMLElement = function HTMLElement() {};
globalThis.setTimeout = () => 0;
globalThis.clearTimeout = () => {};
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};
globalThis.fetch = (url) => {
  const urlStr = String(url);
  fetchCalls.push(urlStr);
  if (errorMode === "unknown_project" && urlStr.includes("project=bad")) {
    return Promise.resolve({
      ok: false,
      status: 404,
      json: async () => ({ code: 404, reason: "unknown_project", error: "Unknown project: bad" }),
    });
  }
  if (errorMode === "project_missing" && urlStr.includes("project=bad")) {
    return Promise.resolve({
      ok: false,
      status: 409,
      json: async () => ({ code: 409, reason: "project_missing", error: "Project directory not found" }),
    });
  }
  if (urlStr === "/api/projects") {
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => ({
        projects: [
          { id: "proj_served", name: "Served Project", path: "/path/served", exists: true },
          { id: "proj_other", name: "Other Project", path: "/path/other", exists: true },
          { id: "proj_missing", name: "Missing Project", path: "/path/missing", exists: false },
        ],
        current: "proj_served",
      }),
    });
  }
  return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
};

let syncError = null;
try {
  vm.runInThisContext(script, { filename: "timeline.html" });
} catch (e) {
  syncError = { name: e && e.name, message: String((e && e.message) || e) };
}

setImmediate(() => {
  const noticeText = elementsById.get("notice-text");
  const noticeBanner = elementsById.get("notice-banner");
  process.stdout.write(JSON.stringify({
    syncError,
    errors,
    fetchCalls,
    historyCalls,
    notice: {
      text: noticeText ? noticeText.textContent : null,
      hidden: noticeBanner ? noticeBanner.hidden : null,
    },
  }));
});
"""


def _run_harness(tmp_path: Path, script: str, search_param: str = "", error_mode: str = "") -> dict:
    harness = tmp_path / "harness_boot.js"
    harness.write_text(HARNESS_BOOT_JS, encoding="utf-8")
    script_file = tmp_path / f"page_script_{error_mode or 'default'}.js"
    script_file.write_text(script, encoding="utf-8")

    proc = subprocess.run(
        [NODE, str(harness), str(script_file), search_param, error_mode],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_timeline_page_script_fetches_projects_runs_and_timeline_on_boot(
    tmp_path: Path, timeline_client
) -> None:
    resp = timeline_client.get("/timeline")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    blocks = re.findall(r"<script>(.*?)</script>", html, flags=re.DOTALL)
    assert blocks, "timeline page has no inline <script>"
    script = max(blocks, key=len)

    result = _run_harness(tmp_path, script)

    assert result["syncError"] is None, result
    assert "/api/projects" in result["fetchCalls"], result
    assert "/api/runs" in result["fetchCalls"], result
    assert "/api/timeline" in result["fetchCalls"], result


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_timeline_page_script_uses_project_from_search(
    tmp_path: Path, timeline_client
) -> None:
    resp = timeline_client.get("/timeline")
    html = resp.get_data(as_text=True)
    script = max(re.findall(r"<script>(.*?)</script>", html, flags=re.DOTALL), key=len)

    result = _run_harness(tmp_path, script, search_param="?project=proj_other")

    assert result["syncError"] is None, result
    assert "/api/projects" in result["fetchCalls"], result
    assert "/api/runs?project=proj_other" in result["fetchCalls"], result
    assert "/api/timeline?project=proj_other" in result["fetchCalls"], result


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_timeline_page_script_handles_unknown_project_error_and_reverts(
    tmp_path: Path, timeline_client
) -> None:
    resp = timeline_client.get("/timeline")
    html = resp.get_data(as_text=True)
    script = max(re.findall(r"<script>(.*?)</script>", html, flags=re.DOTALL), key=len)

    result = _run_harness(
        tmp_path, script, search_param="?project=bad", error_mode="unknown_project"
    )

    assert result["syncError"] is None, result
    assert result["notice"]["text"] == "Unknown project: bad"
    assert result["notice"]["hidden"] is False
    # Reverts back to served project without ?project= in URL
    assert any("/timeline" in call for call in result["historyCalls"])


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_timeline_page_script_handles_missing_project_error_and_reverts(
    tmp_path: Path, timeline_client
) -> None:
    resp = timeline_client.get("/timeline")
    html = resp.get_data(as_text=True)
    script = max(re.findall(r"<script>(.*?)</script>", html, flags=re.DOTALL), key=len)

    result = _run_harness(
        tmp_path, script, search_param="?project=bad", error_mode="project_missing"
    )

    assert result["syncError"] is None, result
    assert result["notice"]["text"] == "Project directory not found"
    assert result["notice"]["hidden"] is False
    assert any("/timeline" in call for call in result["historyCalls"])
