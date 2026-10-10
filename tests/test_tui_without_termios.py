"""A CLI precisa importar mesmo sem termios e tty (Windows): a TUI só perde a leitura de teclas."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SCRIPT = """
import sys
for name in ("termios", "tty"):
    sys.modules[name] = None  # import termios -> ImportError, como no Windows
import meister.cli
from meister.herdr import tui

assert tui.termios is None and tui.tty is None
assert tui.check_key_press() is None  # sem terminal e sem termios: nunca levanta
tui.run_tui_loop(poll_interval=0, max_iterations=1)
print("ok")
"""


def test_cli_and_tui_import_without_termios_and_tty():
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
