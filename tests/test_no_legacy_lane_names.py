from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
LEGACY_NAMES = ("copilot_luna", "codex_luna", "agy_gemini_flash", "claude_sonnet")

# Histórico: o CHANGELOG descreve a renomeação e os registros de run mantêm os nomes como foram gravados.
ALLOWED_PREFIXES = ("docs/superpowers/",)
ALLOWED_FILES = {
    "CHANGELOG.md",
    "CHANGELOG.pt-BR.md",
    "docs/models-and-costs.md",
    "docs/pt-BR/MODELOS_E_CUSTOS.md",
    "tests/test_no_legacy_lane_names.py",
}


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def _is_scanned(rel: str) -> bool:
    if rel in ALLOWED_FILES or rel.startswith(ALLOWED_PREFIXES):
        return False
    name = Path(rel).name
    return not (name.startswith("CHANGELOG") and name.endswith(".md"))


def test_no_legacy_lane_names_in_tracked_files():
    offenders: list[str] = []
    for rel in _tracked_files():
        if not _is_scanned(rel):
            continue
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for name in LEGACY_NAMES:
                if name in line:
                    offenders.append(f"{rel}:{lineno}: {name}")
    assert not offenders, "nomes antigos de via ainda citados:\n" + "\n".join(offenders)
