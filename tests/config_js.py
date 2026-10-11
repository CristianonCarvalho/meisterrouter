"""Auxiliar para testar, via `node`, os helpers puros de config.html.

O bloco entre `// helpers:begin` e `// helpers:end` não pode depender de `window` nem de `document`:
é o que permite executá-lo num subprocesso `node`, sem navegador nem servidor.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

from tests.timeline_js import extract_helpers as _extract_helpers

CONFIG_TEMPLATE = (
    pathlib.Path(__file__).resolve().parent.parent / "meister" / "dashboard" / "templates" / "config.html"
)


def extract_helpers(html_path: str | pathlib.Path | None = None) -> str:
    return _extract_helpers(html_path if html_path is not None else CONFIG_TEMPLATE)


def run_js(expression: str, text: dict | None = None) -> object:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node não está no PATH")
    script = "\n".join([
        f"const text = {json.dumps(text or {}, ensure_ascii=False)};",
        extract_helpers(),
        f"process.stdout.write(JSON.stringify(({expression})));",
    ])
    result = subprocess.run(
        [node, "-"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"node falhou ({result.returncode}): {result.stderr.strip()}")
    return json.loads(result.stdout)
