"""Auxiliar para testar, via `node`, os helpers puros de timeline.html.

O bloco entre `// helpers:begin` e `// helpers:end` não pode depender de `window` nem de `document`:
é o que permite executá-lo num subprocesso `node`, sem navegador nem servidor.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

TIMELINE_TEMPLATE = (
    pathlib.Path(__file__).resolve().parent.parent / "meister" / "dashboard" / "templates" / "timeline.html"
)
BEGIN_MARKER = "// helpers:begin"
END_MARKER = "// helpers:end"


def extract_helpers(html_path: str | pathlib.Path | None = None) -> str:
    path = pathlib.Path(html_path) if html_path is not None else TIMELINE_TEMPLATE
    source = path.read_text(encoding="utf-8")
    begin_count = source.count(BEGIN_MARKER)
    end_count = source.count(END_MARKER)
    if begin_count != 1 or end_count != 1:
        raise ValueError(
            f"{path}: esperado exatamente um '{BEGIN_MARKER}' e um '{END_MARKER}', "
            f"encontrado {begin_count} e {end_count}"
        )
    begin = source.index(BEGIN_MARKER) + len(BEGIN_MARKER)
    end = source.index(END_MARKER)
    if end < begin:
        raise ValueError(f"{path}: '{END_MARKER}' aparece antes de '{BEGIN_MARKER}'")
    return source[begin:end].strip("\n")


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
