from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from meister.dashboard.server import app

NODE = shutil.which("node")

HARNESS_JS = r"""
const fs = require("fs");
const vm = require("vm");

const [scriptPath, storageMode] = process.argv.slice(2);
const script = fs.readFileSync(scriptPath, "utf8");
const errors = [];
const fetchCalls = [];

process.on("unhandledRejection", (reason) => {
  errors.push({
    kind: "rejection",
    name: reason && reason.name,
    message: String((reason && reason.message) || reason),
  });
});

// Universal fake element: accepts any property, method or call.
function makeElement() {
  const cache = {};
  const target = function () {};
  return new Proxy(target, {
    get(_t, prop) {
      if (typeof prop === "symbol" || prop === "then") return undefined;
      if (prop === "toString" || prop === "valueOf") return () => "";
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

const memoryStorage = new Map();
const localStorage =
  storageMode === "storage-throws"
    ? {
        getItem() { throw new Error("storage blocked"); },
        setItem() { throw new Error("storage blocked"); },
      }
    : {
        getItem: (k) => (memoryStorage.has(k) ? memoryStorage.get(k) : null),
        setItem: (k, v) => memoryStorage.set(k, String(v)),
      };

const window = {
  location: { hash: "", pathname: "/timeline", search: "" },
  history: { replaceState() {} },
  localStorage,
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
  fetchCalls.push(String(url));
  return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
};

let syncError = null;
try {
  vm.runInThisContext(script, { filename: "timeline.html" });
} catch (e) {
  syncError = { name: e && e.name, message: String((e && e.message) || e) };
}

setImmediate(() => {
  process.stdout.write(JSON.stringify({ syncError, errors, fetchCalls }));
});
"""


@pytest.fixture
def timeline_html(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".meister").mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("MEISTER_LOG_DIR", str(log_dir))
    resp = app.test_client().get("/timeline")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _main_inline_script(html: str) -> str:
    blocks = re.findall(r"<script>(.*?)</script>", html, flags=re.DOTALL)
    assert blocks, "timeline page has no inline <script>"
    return max(blocks, key=len)


def _run_page_script(tmp_path: Path, script: str, storage_mode: str) -> dict:
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS_JS, encoding="utf-8")
    script_file = tmp_path / f"page-{storage_mode}.js"
    script_file.write_text(script, encoding="utf-8")
    proc = subprocess.run(
        [NODE, str(harness), str(script_file), storage_mode],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("storage_mode", ["storage-ok", "storage-throws"])
def test_timeline_page_script_boots_without_errors(
    tmp_path: Path, timeline_html: str, storage_mode: str
):
    script = _main_inline_script(timeline_html)

    result = _run_page_script(tmp_path, script, storage_mode)

    assert result["syncError"] is None, result
    assert result["errors"] == [], result
    assert "/api/runs" in result["fetchCalls"], result
    assert "/api/timeline" in result["fetchCalls"], result
