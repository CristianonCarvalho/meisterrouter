"""Guarda: testes não podem trocar funções do módulo `time` no processo inteiro.

Trocar `time.sleep` (ou `monotonic`/`time`) com `monkeypatch.setattr(time, ...)` afeta todas as
threads do processo; com pytest-xdist isso derruba os workers do execnet. Use uma costura do
próprio módulo testado (por exemplo `monkeypatch.setattr(wait_cmd, "_sleep", ...)`).
"""

import re
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
THIS_FILE = Path(__file__).resolve()

GLOBAL_TIME_PATCH = re.compile(
    r"""setattr\(\s*(?:time\s*,\s*["'](?:sleep|monotonic|time)["']|["']time\.(?:sleep|monotonic|time)["'])"""
)


def find_global_time_patches(root):
    """Devolve `arquivo:linha: trecho` de cada troca global do módulo `time` encontrada em `root`."""
    offenders = []
    for path in sorted(Path(root).rglob("*.py")):
        if path.resolve() == THIS_FILE:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            if GLOBAL_TIME_PATCH.search(line):
                offenders.append(f"{path}:{number}: {line.strip()}")
    return offenders


def test_no_test_patches_global_time_functions():
    offenders = find_global_time_patches(TESTS_DIR)
    assert offenders == [], "troca global do módulo time (use uma costura do módulo):\n" + "\n".join(offenders)


@pytest.mark.parametrize(
    "line",
    [
        'monkeypatch.setattr(time, "sleep", fake)',
        "monkeypatch.setattr(time, 'monotonic', fake)",
        'monkeypatch.setattr(time, "time", fake)',
        'monkeypatch.setattr("time.sleep", fake)',
    ],
)
def test_guard_detects_global_time_patch(tmp_path, line):
    (tmp_path / "test_bad.py").write_text(f"import time\n{line}\n", encoding="utf-8")
    offenders = find_global_time_patches(tmp_path)
    assert len(offenders) == 1
    assert "test_bad.py:2" in offenders[0]


def test_guard_allows_seam_patch(tmp_path):
    (tmp_path / "test_ok.py").write_text(
        'monkeypatch.setattr(wait_cmd, "_sleep", fake)\npatch("time.sleep")\n', encoding="utf-8"
    )
    assert find_global_time_patches(tmp_path) == []
