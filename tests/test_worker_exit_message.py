from pathlib import Path
from unittest.mock import patch

import pytest

from tests.fake_cli import make_fake_cli
from meister.engine.workers import find_quota_match
from meister.worker import HarnessWorker

# Amostra montada em tempo de execução para não repetir por extenso a frase que o detector reconhece.
QUOTA_SAMPLE = "Error: 429 Too " + "Many Requests"
COMMON_SAMPLE = "build step failed: unexpected token"


def _failing_worker(tmp_path: Path, output: str) -> HarnessWorker:
    body = f"import sys\nprint({output!r})\nsys.exit(1)\n" if output else "import sys\nsys.exit(1)\n"
    cli = make_fake_cli(tmp_path, "exit_message_cli", body)
    worker = HarnessWorker(model="tier_1c", cwd=str(tmp_path))
    worker.cli_binary = str(cli)
    return worker


def _run_failure(tmp_path: Path, output: str) -> tuple[str, str]:
    worker = _failing_worker(tmp_path, output)
    with patch("meister.worker.find_cli_binary", return_value=worker.cli_binary):
        with pytest.raises(RuntimeError) as excinfo:
            worker.run_task("Exit message task", timeout=20)
    return str(excinfo.value), worker.harness


def test_quota_output_appends_matched_snippet_to_exit_message(tmp_path):
    snippet = find_quota_match(QUOTA_SAMPLE)
    assert snippet is not None

    message, harness = _run_failure(tmp_path, QUOTA_SAMPLE)

    assert message == f"Harness {harness} failed with exit code 1: {snippet}"


def test_common_output_keeps_plain_exit_message(tmp_path):
    assert find_quota_match(COMMON_SAMPLE) is None

    message, harness = _run_failure(tmp_path, COMMON_SAMPLE)

    assert message == f"Harness {harness} failed with exit code 1"


def test_empty_output_keeps_plain_exit_message(tmp_path):
    message, harness = _run_failure(tmp_path, "")

    assert message == f"Harness {harness} failed with exit code 1"
