from __future__ import annotations

import json
import os
import re
import sys

import pytest

from tests.fake_cli import make_fake_cli
from meister.config import _parse_config_dict
from meister.harness_effort import HARNESS_EFFORT_VALUES
from meister.i18n import t
from meister.worker import (
    HARNESS_OPENCODE,
    HarnessWorker,
    build_harness_command,
    find_cli_binary,
    resolve_worker_harness_and_model,
)


def _opencode_config(
    harness: str = "opencode",
    model: str = "opencode/big-pickle",
    effort: str | None = None,
):
    tier: dict[str, str] = {"name": "lane-opencode", "harness": harness, "model": model}
    if effort is not None:
        tier["effort"] = effort
    return _parse_config_dict({"router": {"mode": "first"}, "workers": {"tier_order": [tier]}})


def test_opencode_resolves_to_harness():
    config = _opencode_config(harness="opencode")
    assert resolve_worker_harness_and_model("lane-opencode", config=config) == (
        HARNESS_OPENCODE,
        "opencode/big-pickle",
    )


def test_opencode_command_with_model():
    assert build_harness_command(
        HARNESS_OPENCODE, "/cli", "opencode/big-pickle", "prompt", "/work"
    ) == [
        "/cli",
        "run",
        "--auto",
        "-m",
        "opencode/big-pickle",
        "prompt",
    ]


def test_opencode_command_without_model():
    assert build_harness_command(HARNESS_OPENCODE, "/cli", None, "prompt", "/work") == [
        "/cli",
        "run",
        "--auto",
        "prompt",
    ]
    assert build_harness_command(HARNESS_OPENCODE, "/cli", "default", "prompt", "/work") == [
        "/cli",
        "run",
        "--auto",
        "prompt",
    ]


def test_opencode_command_prompt_with_leading_dash():
    cmd = build_harness_command(
        HARNESS_OPENCODE, "/cli", "opencode/big-pickle", "--flag and -opt prompt", "/work"
    )
    assert cmd == [
        "/cli",
        "run",
        "--auto",
        "-m",
        "opencode/big-pickle",
        "--flag and -opt prompt",
    ]
    assert cmd[-1] == "--flag and -opt prompt"


def test_opencode_command_rejects_effort():
    assert HARNESS_OPENCODE not in HARNESS_EFFORT_VALUES
    with pytest.raises(
        ValueError,
        match=re.escape(
            t(
                "tiers.unsupported_effort",
                lane=HARNESS_OPENCODE,
                harness=HARNESS_OPENCODE,
            )
        ),
    ):
        build_harness_command(
            HARNESS_OPENCODE,
            "/cli",
            "opencode/big-pickle",
            "prompt",
            "/work",
            effort="high",
        )


def test_worker_rejects_effort():
    config = _opencode_config(effort="high")
    with pytest.raises(
        ValueError,
        match=re.escape(
            t(
                "tiers.unsupported_effort",
                lane="lane-opencode",
                harness=HARNESS_OPENCODE,
            )
        ),
    ):
        HarnessWorker(model="lane-opencode", config=config)


def test_find_cli_binary_finds_fake_opencode_on_path(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    executable = make_fake_cli(bin_dir, "opencode", "print('ok')\n")
    monkeypatch.setenv("PATH", str(bin_dir))
    if sys.platform == "win32":
        assert executable.name == "opencode.cmd"

    found = find_cli_binary(HARNESS_OPENCODE)

    assert found is not None
    assert found.casefold() == str(executable).casefold()


def test_find_cli_binary_not_found(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda *args, **kwargs: None)
    orig_exists = os.path.exists
    monkeypatch.setattr(
        "os.path.exists",
        lambda p: False if "opencode" in str(p) else orig_exists(p),
    )
    assert find_cli_binary(HARNESS_OPENCODE) is None


def test_worker_runs_fake_opencode(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    argv_file = tmp_path / "argv.json"
    executable = make_fake_cli(
        tmp_path / "bin",
        "opencode",
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(argv_file)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        "pathlib.Path('opencode_out.txt').write_text('done', encoding='utf-8')\n"
        "print('resposta final')\n",
    )
    monkeypatch.setattr("meister.worker.find_cli_binary", lambda _harness: str(executable))

    result = HarnessWorker(
        model="lane-opencode",
        cwd=str(work_dir),
        config=_opencode_config(harness="opencode", model="opencode/big-pickle"),
    ).run_task("test task")

    argv = json.loads(argv_file.read_text(encoding="utf-8"))
    assert argv[:3] == ["run", "--auto", "-m"]
    assert argv[3] == "opencode/big-pickle"
    assert argv[-1].startswith("Task: test task")
    assert (work_dir / "opencode_out.txt").read_text(encoding="utf-8") == "done"
    assert result["exit_code"] == 0
    assert result["harness"] == HARNESS_OPENCODE
    assert "resposta final" in result["output"]


def test_worker_runs_fake_opencode_default_model(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    argv_file = tmp_path / "argv.json"
    executable = make_fake_cli(
        tmp_path / "bin",
        "opencode",
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(argv_file)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        "print('resposta final')\n",
    )
    monkeypatch.setattr("meister.worker.find_cli_binary", lambda _harness: str(executable))

    result = HarnessWorker(
        model="lane-opencode",
        cwd=str(work_dir),
        config=_opencode_config(harness="opencode", model="default"),
    ).run_task("-prompt-with-dash")

    argv = json.loads(argv_file.read_text(encoding="utf-8"))
    assert argv[:2] == ["run", "--auto"]
    assert "-m" not in argv
    assert argv[-1].startswith("Task: -prompt-with-dash")
    assert result["exit_code"] == 0
    assert result["harness"] == HARNESS_OPENCODE
