import json
import os
import subprocess
import sys

import pytest
from click.testing import CliRunner

from meister.cli import main
from meister.config import DocsOnlyGateConfig, GateCommand, MeisterConfig, load_config, validate_config
from meister.gate import DeterministicGate, VerificationResult, is_docs_only_change
from meister.logger import add_event_observer, remove_event_observer
from meister.worktree import IntegrationPipeline, WorktreeManager


DEFAULT_DOC_PATHS = ["*.md", "docs/**/*.md", "docs/img/**"]


@pytest.mark.parametrize(
    ("changed_files", "patterns", "expected"),
    [
        (["README.md", "docs/a/b.md", "docs/img/x.gif"], DEFAULT_DOC_PATHS, True),
        (["README.md", "src/app.py"], DEFAULT_DOC_PATHS, False),
        ([], DEFAULT_DOC_PATHS, False),
        (["docs/../src/app.py"], ["**/*"], False),
        (["/etc/x.md"], ["**/*"], False),
        ([""], ["**/*"], False),
        (["docs/script.py"], DEFAULT_DOC_PATHS, False),
        (["guide.txt"], ["*.txt"], True),
        (["docs/sub/guide.md"], ["docs/**/*.md"], True),
    ],
)
def test_is_docs_only_change_requires_all_safe_paths_to_match(changed_files, patterns, expected):
    assert is_docs_only_change(changed_files, patterns) is expected


def test_docs_only_defaults_are_disabled_with_expected_paths():
    config = load_config()

    assert config.gate.docs_only.enabled is False
    assert config.gate.docs_only.paths == DEFAULT_DOC_PATHS
    assert config.gate.docs_only.commands == []


@pytest.mark.parametrize(
    "paths",
    [
        "[]",
        '["/absolute/path.md"]',
        '["C:\\\\absolute\\\\path.md"]',
        '["docs/../secret.md"]',
        '[""]',
    ],
)
def test_invalid_docs_only_paths_are_configuration_errors(tmp_path, paths):
    config_file = tmp_path / "invalid_docs_paths.yaml"
    config_file.write_text(f"gate:\n  docs_only:\n    paths: {paths}\n", encoding="utf-8")

    issues = validate_config(load_config(str(config_file)))

    assert any(issue.level == "error" and issue.path == "gate.docs_only.paths" for issue in issues)


@pytest.mark.parametrize("enabled", ['"true"', "1", "null"])
def test_docs_only_enabled_must_be_boolean(tmp_path, enabled):
    config_file = tmp_path / "invalid_docs_enabled.yaml"
    config_file.write_text(
        f"gate:\n  docs_only:\n    enabled: {enabled}\n",
        encoding="utf-8",
    )

    issues = validate_config(load_config(str(config_file)))

    assert any(
        issue.level == "error" and issue.path == "gate.docs_only.enabled"
        for issue in issues
    )


def test_docs_only_enabled_requires_commands_and_uses_gate_command_validation(tmp_path):
    empty_file = tmp_path / "empty_docs_gate.yaml"
    empty_file.write_text("gate:\n  docs_only:\n    enabled: true\n", encoding="utf-8")
    empty_issues = validate_config(load_config(str(empty_file)))
    assert any(
        issue.level == "error"
        and issue.path == "gate.docs_only.commands"
        and "obrigatório quando enabled" in issue.message
        for issue in empty_issues
    )

    invalid_file = tmp_path / "invalid_docs_command.yaml"
    invalid_file.write_text(
        """
gate:
  docs_only:
    enabled: true
    commands:
      - {name: docs, run: "pytest", timeout_seconds: 0}
""",
        encoding="utf-8",
    )
    issues = validate_config(load_config(str(invalid_file)))

    assert any(
        issue.level == "error"
        and issue.path == "gate.docs_only.commands[0].timeout_seconds"
        for issue in issues
    )

    duplicate_file = tmp_path / "duplicate_docs_commands.yaml"
    duplicate_file.write_text(
        """
gate:
  docs_only:
    enabled: true
    commands:
      - {name: docs, run: "pytest"}
      - {name: docs, run: "pytest"}
""",
        encoding="utf-8",
    )
    issues = validate_config(load_config(str(duplicate_file)))
    assert any(
        issue.level == "error"
        and issue.path == "gate.docs_only.commands[1].name"
        and "repetido" in issue.message
        for issue in issues
    )


def test_docs_only_config_is_visible_in_config_show_text_and_json(tmp_path):
    config_file = tmp_path / "docs_gate.yaml"
    config_file.write_text(
        """
gate:
  docs_only:
    enabled: true
    paths: ["*.md", "docs/**/*.md"]
    commands:
      - {name: docs, run: "pytest tests/test_docs_links.py"}
""",
        encoding="utf-8",
    )
    runner = CliRunner()

    text_result = runner.invoke(main, ["config", "show", "--config-path", str(config_file)])
    json_result = runner.invoke(main, ["config", "show", "--config-path", str(config_file), "--json"])

    assert text_result.exit_code == 0
    assert "Docs-only enabled: True" in text_result.output
    assert "Docs-only paths: *.md, docs/**/*.md" in text_result.output
    assert "Docs-only command docs:" in text_result.output
    assert json_result.exit_code == 0
    docs_only = json.loads(json_result.output)["gate"]["docs_only"]
    assert docs_only["enabled"] is True
    assert docs_only["paths"] == ["*.md", "docs/**/*.md"]
    assert docs_only["commands"][0]["name"] == "docs"


def test_docs_only_and_full_gate_cache_are_isolated_by_mode(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, text=True)
    counter = tmp_path / "counter.txt"
    config = MeisterConfig()
    config.environment.install_dependencies = False
    config.gate.cache = True

    def command(name):
        code = (
            "import sys; "
            "open(sys.argv[1], 'a', encoding='utf-8').write(sys.argv[2] + '\\n')"
        )
        return GateCommand(
            name=name,
            run=[sys.executable, "-c", code, str(counter), name],
            timeout_seconds=10,
            required=True,
        )

    shared_command = command("same")
    config.gate.commands = [shared_command]
    config.gate.docs_only = DocsOnlyGateConfig(
        enabled=True,
        paths=DEFAULT_DOC_PATHS,
        commands=[shared_command],
    )
    gate = DeterministicGate(str(repo), config=config)

    first_docs = gate.run_verification_ex(docs_only=True)
    first_full = gate.run_verification_ex(docs_only=False)
    cached_docs = gate.run_verification_ex(docs_only=True)
    cached_full = gate.run_verification_ex(docs_only=False)

    assert all(result.passed for result in (first_docs, first_full, cached_docs, cached_full))
    assert not first_docs.cached and not first_full.cached
    assert cached_docs.cached and cached_full.cached
    assert counter.read_text(encoding="utf-8").splitlines() == ["same", "same"]


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )


def _init_repo(repo, docs_only_enabled=True):
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "docs-gate@meisterrouter.local")
    _git(repo, "config", "user.name", "Meister Docs Gate")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    (repo / "meister.config.yaml").write_text(
        f"""
gate:
  docs_only:
    enabled: {str(docs_only_enabled).lower()}
    paths: ["*.md", "docs/**/*.md", "docs/img/**"]
    commands:
      - {{name: docs, run: "true"}}
""",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")


class _RecordingGate:
    def __init__(self):
        self.modes = []

    def run_verification_ex(self, repo_path=None, docs_only=False):
        self.modes.append(docs_only)
        return VerificationResult(True, "ok")


def test_pipeline_selects_docs_gate_only_before_and_after_merge_not_final(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "logs"))
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "worktrees"))
    fake_gate = _RecordingGate()
    pipeline = IntegrationPipeline(manager, gate=fake_gate)
    integration = pipeline.start_integration("docs-only-run")
    worker = manager.create_worktree("docs-task", base_ref=integration.branch_name)
    with open(os.path.join(worker.worktree_path, "README.md"), "a", encoding="utf-8") as stream:
        stream.write("docs update\n")

    events = []
    observer = events.append
    add_event_observer(observer)
    try:
        prepared = pipeline.prepare_subtask(worker, target_files=["README.md"], task_id="docs-task")
        assert prepared.commit_sha
        passed, _ = pipeline.merge_prepared(prepared)
        assert passed
        assert pipeline.validate_final_integration()[0]

        mixed_worker = manager.create_worktree(
            "mixed-task",
            base_ref=integration.branch_name,
        )
        with open(os.path.join(mixed_worker.worktree_path, "README.md"), "a", encoding="utf-8") as stream:
            stream.write("more docs\n")
        source_dir = os.path.join(mixed_worker.worktree_path, "src")
        os.makedirs(source_dir)
        with open(os.path.join(source_dir, "app.py"), "w", encoding="utf-8") as stream:
            stream.write("print('code')\n")
        mixed_prepared = pipeline.prepare_subtask(
            mixed_worker,
            target_files=["README.md", "src/app.py"],
            task_id="mixed-task",
        )
        assert mixed_prepared.commit_sha
        assert pipeline.merge_prepared(mixed_prepared)[0]
        manager.cleanup_worktree(mixed_worker.task_id, force=True)

        assert fake_gate.modes == [True, True, False, False, False]
        gate_events = [
            event for event in events
            if event.get("event") == "worker_phase" and event.get("phase") == "gate"
        ]
        assert [event["gate_mode"] for event in gate_events] == [
            "docs_only", "docs_only", "full", "full", "full"
        ]
    finally:
        remove_event_observer(observer)
        manager.cleanup_worktree("docs-task", force=True)
        pipeline.abort_integration()


def test_pipeline_disables_docs_mode_when_opt_in_is_false(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo, docs_only_enabled=False)
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "logs"))
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "worktrees"))
    fake_gate = _RecordingGate()
    pipeline = IntegrationPipeline(manager, gate=fake_gate)

    result = pipeline._run_gate(
        str(repo),
        changed_files=["README.md"],
        allow_docs_only=True,
    )

    assert result.passed
    assert fake_gate.modes == [False]
