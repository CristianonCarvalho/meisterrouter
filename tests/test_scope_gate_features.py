import json
import os
import subprocess
import sys

from click.testing import CliRunner

from meister.cli import main
from meister.config import load_config, validate_config
from meister.gate import DeterministicGate
from meister.worktree import IntegrationPipeline, WorktreeManager, scope_violations


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _repo(path, files):
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "Tests")
    for name, content in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    _git(path, "add", ".")
    _git(path, "commit", "-m", "initial")
    return path


def _fake_executable(directory, name, body):
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / name
    executable.write_text(f"#!{sys.executable}\n{body}\n")
    executable.chmod(0o755)
    return executable


def _fake_path(tmp_path, monkeypatch, *, manager_exit=0, tool_exit=0):
    binary_dir = tmp_path / "fake-bin"
    install_script = (
        "import os, pathlib, sys\n"
        "with open(os.environ['FAKE_CALL_LOG'], 'a') as f: f.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[0].endswith(('npm', 'pnpm', 'yarn')) and 'install' in sys.argv:\n"
        " p=pathlib.Path.cwd()/'node_modules/.bin'; p.mkdir(parents=True, exist_ok=True)\n"
        " for n in ('eslint','vitest'):\n"
        "  q=p/n; q.write_text('#!" + sys.executable + "\\nimport os,sys\\nwith open(os.environ[\\'FAKE_CALL_LOG\\'],\\'a\\') as f: f.write(\\'local-\\'+os.path.basename(sys.argv[0])+\\'\\\\n\\')\\nsys.exit("
        + str(tool_exit)
        + ")\\n'); q.chmod(0o755)\n"
        f"sys.exit({manager_exit})"
    )
    for executable in ("npm", "pnpm", "yarn"):
        _fake_executable(binary_dir, executable, install_script)
    _fake_executable(
        binary_dir,
        "npx",
        "raise SystemExit('npx must never be invoked')",
    )
    monkeypatch.setenv("PATH", str(binary_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_CALL_LOG", str(tmp_path / "calls.log"))
    return tmp_path / "calls.log"


def test_scope_globs_generated_files_and_gitignored_paths(tmp_path):
    repo = _repo(
        tmp_path / "repo",
        {
            ".gitignore": "ignored.txt\ntracked-ignored.txt\n",
            "src/old.py": "old\n",
            "drizzle/old.sql": "old\n",
            "tracked-ignored.txt": "tracked\n",
        },
    )
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "worktrees"))
    wt = manager.create_worktree("scope-test")
    for path in ("drizzle/new.sql", "src/nested/file.ts", "nested/cache.tsbuildinfo", "pnpm-lock.yaml"):
        target = os.path.join(wt.worktree_path, path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as output:
            output.write("generated\n")
    with open(os.path.join(wt.worktree_path, "outside.py"), "w") as output:
        output.write("outside\n")
    with open(os.path.join(wt.worktree_path, "ignored.txt"), "w") as output:
        output.write("ignored\n")
    with open(os.path.join(wt.worktree_path, "tracked-ignored.txt"), "w") as output:
        output.write("ignored tracked\n")

    valid, invalid = manager.verify_scope(
        wt.worktree_path,
        ["drizzle/*", "src/**", "dir/"],
        base_ref=wt.base_commit,
    )
    assert not valid
    assert invalid == ["outside.py"]
    os.remove(os.path.join(wt.worktree_path, "outside.py"))
    valid, invalid = manager.verify_scope(
        wt.worktree_path,
        ["drizzle/*", "src/**"],
        base_ref=wt.base_commit,
        tolerated=["pnpm-lock.yaml", "*.tsbuildinfo"],
    )
    assert valid and invalid == [], invalid
    valid, invalid = manager.verify_scope(
        wt.worktree_path,
        ["src/**"],
        base_ref=wt.base_commit,
        tolerated=["pnpm-lock.yaml", "*.tsbuildinfo"],
    )
    assert "pnpm-lock.yaml" not in invalid and "nested/cache.tsbuildinfo" not in invalid
    assert manager.verify_scope(wt.worktree_path, [], base_ref=wt.base_commit) == (True, [])


def test_scope_patterns_match_next_dynamic_routes_and_character_classes(tmp_path):
    routes = [
        ("src/app/leads/[id]/page.tsx", "src/app/leads/[id]/page.tsx"),
        ("src/app/leads/[id]/**", "src/app/leads/[id]/page.tsx"),
        ("src/app/[slug]/*.tsx", "src/app/[slug]/page.tsx"),
        ("src/app/[...rest]/**", "src/app/[...rest]/page.tsx"),
        ("src/app/[[...opt]]/**", "src/app/[[...opt]]/page.tsx"),
        ("src/[ab]*.py", "src/a_file.py"),
    ]
    for pattern, path in routes:
        assert scope_violations([path], [pattern]) == []
    assert scope_violations(
        ["src/app/leads/[other]/page.tsx"],
        ["src/app/leads/[id]/page.tsx"],
    ) == ["src/app/leads/[other]/page.tsx"]
    # colchetes aninhados valem so como texto literal (nao como classe de caracteres)
    assert scope_violations(
        ["src/app/o]/page.tsx"],
        ["src/app/[[...opt]]/**"],
    ) == ["src/app/o]/page.tsx"]
    assert scope_violations(
        ["src/app/leads/[id]/page.tsx"],
        ["src/app/leads/[other]/page.tsx"],
    ) == ["src/app/leads/[id]/page.tsx"]
    assert scope_violations(
        ["src/app/[slug]/page.tsx"],
        ["unrelated/**"],
        tolerated_files=["src/app/[slug]/*.tsx"],
    ) == []

    repo = _repo(tmp_path / "route-repo", {"src/app/leads/[id]/page.tsx": "old\n"})
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "route-worktrees"))
    worktree = manager.create_worktree("route-scope")
    route_file = os.path.join(worktree.worktree_path, "src/app/leads/[id]/page.tsx")
    with open(route_file, "w") as output:
        output.write("updated\n")
    assert manager.verify_scope(
        worktree.worktree_path,
        ["src/app/leads/[id]/page.tsx"],
        base_ref=worktree.base_commit,
    ) == (True, [])
    assert manager.verify_scope(
        worktree.worktree_path,
        ["src/app/leads/[other]/page.tsx"],
        base_ref=worktree.base_commit,
    ) == (False, ["src/app/leads/[id]/page.tsx"])


def test_scope_directory_pattern_and_user_tolerance_replaces_defaults(tmp_path):
    repo = _repo(tmp_path / "repo", {"src/a.py": "a\n"})
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "wts"))
    wt = manager.create_worktree("directory")
    os.makedirs(os.path.join(wt.worktree_path, "dir/sub"))
    with open(os.path.join(wt.worktree_path, "dir/sub/file.txt"), "w") as output:
        output.write("x")
    assert manager.verify_scope(wt.worktree_path, ["dir/"], base_ref=wt.base_commit)[0]
    assert not manager.verify_scope(
        wt.worktree_path, ["src/**"], base_ref=wt.base_commit, tolerated=["custom/**"]
    )[0]


def test_config_new_sections_parse_validate_and_show(tmp_path):
    config = tmp_path / "meister.config.yaml"
    config.write_text(
        """
retry: {pane_lost_attempts: 2, pane_lost_backoff_seconds: 0}
scope: {tolerated_files: [custom.lock]}
environment: {install_dependencies: false, install_timeout_seconds: 12}
gate:
  install: "go mod download"
  commands:
    - {name: unit, run: "go test ./...", timeout_seconds: 30, required: true}
  allow_unverified: true
"""
    )
    parsed = load_config(str(config))
    assert parsed.scope.tolerated_files == ["custom.lock"]
    assert parsed.environment.install_dependencies is False
    assert parsed.gate.commands[0].name == "unit"
    assert not [issue for issue in validate_config(parsed) if issue.level == "error"]
    result = CliRunner().invoke(main, ["config", "show", "--config-path", str(config), "--json"])
    assert result.exit_code == 0, result.output
    shown = json.loads(result.output)
    assert shown["scope"]["tolerated_files"] == ["custom.lock"]
    assert shown["retry"] == {
        "pane_lost_attempts": 2,
        "pane_lost_backoff_seconds": 0.0,
    }
    assert shown["gate"]["commands"][0]["name"] == "unit"
    assert shown["environment"]["install_timeout_seconds"] == 12

    invalid = tmp_path / "invalid.yaml"
    invalid.write_text('environment: {install_dependencies: "yes"}\ngate: {allow_unverified: 1}\n')
    errors = [issue for issue in validate_config(load_config(str(invalid))) if issue.level == "error"]
    assert any(issue.path == "environment.install_dependencies" for issue in errors)
    assert any(issue.path == "gate.allow_unverified" for issue in errors)


def test_config_defaults_include_generated_lockfiles():
    defaults = load_config()
    assert {
        "pnpm-lock.yaml", "package-lock.json", "yarn.lock", "poetry.lock", "uv.lock",
        "*.tsbuildinfo", ".vitest/**",
    } <= set(defaults.scope.tolerated_files)


def test_node_gate_installs_before_local_eslint_without_npx(tmp_path, monkeypatch):
    log = _fake_path(tmp_path, monkeypatch)
    project = tmp_path / "node"
    project.mkdir()
    (project / "package.json").write_text('{"devDependencies":{"eslint":"1"},"scripts":{"test":"test"}}')
    (project / "pnpm-lock.yaml").write_text("lock")
    (project / ".eslintrc.json").write_text("{}")
    gate = DeterministicGate(str(project))
    result = gate.run_verification_ex()
    assert result.passed and not result.infrastructure_error
    calls = log.read_text().splitlines()
    assert calls[0] == "install --frozen-lockfile"
    assert "local-eslint" in calls
    assert all("npx" not in call for call in calls)


def test_node_manager_selection_and_install_failure(tmp_path, monkeypatch):
    log = _fake_path(tmp_path, monkeypatch)
    project = tmp_path / "node"
    project.mkdir()
    (project / "package.json").write_text('{"scripts":{"test":"jest"}}')
    (project / "package-lock.json").write_text("lock")
    config = tmp_path / "meister.config.yaml"
    config.write_text("environment: {install_dependencies: true}\n")
    # npm ci is exercised through the shared environment helper by a configured command.
    config.write_text(
        "gate:\n  commands: [{name: check, run: 'python -c \"print(1)\"', timeout_seconds: 5, required: true}]\n"
    )
    result = DeterministicGate(str(project), load_config(str(config))).run_verification_ex()
    assert result.passed
    assert log.read_text().splitlines()[0] == "ci"

    failing_bin = tmp_path / "failing-bin"
    _fake_executable(
        failing_bin,
        "npm",
        "import sys\nsys.exit(9)",
    )
    monkeypatch.setenv("PATH", str(failing_bin) + os.pathsep + os.environ.get("PATH", ""))
    (project / ".eslintrc.json").write_text("{}")
    result = DeterministicGate(str(project)).run_verification_ex()
    assert not result.passed and result.infrastructure_error


def test_configured_gate_semantics_and_no_shell(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    calls = tmp_path / "custom.log"
    _fake_executable(
        bin_dir,
        "go",
        "import os,sys\nwith open(os.environ['CUSTOM_LOG'],'a') as f: f.write('go '+ ' '.join(sys.argv[1:])+'\\n')\nsys.exit(1 if 'fail' in sys.argv or 'sentinel' in sys.argv else 0)",
    )
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("CUSTOM_LOG", str(calls))
    project = tmp_path / "go-project"
    project.mkdir()
    (project / "go.mod").write_text("module example\n")
    config = tmp_path / "gate.yaml"
    config.write_text(
        """
environment: {install_dependencies: false}
gate:
  commands:
    - {name: unit, run: "go test ./...", timeout_seconds: 5, required: true}
    - {name: optional, run: "go fail", timeout_seconds: 5, required: false}
"""
    )
    result = DeterministicGate(str(project), load_config(str(config))).run_verification_ex()
    assert result.passed and "[WARN optional]" in result.output
    assert calls.read_text().splitlines() == ["go test ./...", "go fail"]

    (project / "sentinel").write_text("")
    config.write_text(
        "environment: {install_dependencies: false}\n"
        "gate:\n  commands:\n"
        "    - {name: unsafe, run: 'go test ./...; touch sentinel', timeout_seconds: 5, required: true}\n"
    )
    result = DeterministicGate(str(project), load_config(str(config))).run_verification_ex()
    assert not result.passed
    assert not (project / "sentinel").read_text()


def test_configured_gate_missing_executable_timeout_and_unverified(tmp_path):
    project = tmp_path / "go"
    project.mkdir()
    (project / "go.mod").write_text("module example\n")
    missing = tmp_path / "missing.yaml"
    missing.write_text(
        "environment: {install_dependencies: false}\n"
        "gate:\n  commands: [{name: missing, run: 'missing-command-xyz', timeout_seconds: 2, required: true}]\n"
    )
    result = DeterministicGate(str(project), load_config(str(missing))).run_verification_ex()
    assert result.infrastructure_error

    sleepy = tmp_path / "sleep"
    _fake_executable(sleepy, "slow", "import time\ntime.sleep(1)")
    timeout_config = tmp_path / "timeout.yaml"
    timeout_config.write_text(
        "environment: {install_dependencies: false}\n"
        "gate:\n  commands: [{name: slow, run: slow, timeout_seconds: 0.01, required: true}]\n"
    )
    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = str(sleepy) + os.pathsep + old_path
    try:
        result = DeterministicGate(str(project), load_config(str(timeout_config))).run_verification_ex()
    finally:
        os.environ["PATH"] = old_path
    assert result.infrastructure_error

    unconfigured = tmp_path / "unverified.yaml"
    unconfigured.write_text("gate: {allow_unverified: true}\n")
    result = DeterministicGate(str(project), load_config(str(unconfigured))).run_verification_ex()
    assert result.passed and "[UNVERIFIED]" in result.output
    unconfigured.write_text("gate: {allow_unverified: false}\n")
    result = DeterministicGate(str(project), load_config(str(unconfigured))).run_verification_ex()
    assert not result.passed and "Configure gate.commands" in result.output


def test_worktree_setup_prepares_node_only_and_tolerates_failure(tmp_path, monkeypatch):
    calls = _fake_path(tmp_path, monkeypatch)
    repo = _repo(
        tmp_path / "node-repo",
        {"package.json": '{"packageManager":"pnpm@9.0.0"}', "pnpm-lock.yaml": "lock"},
    )
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "node-worktrees"))
    info = manager.create_worktree("node-task")
    assert calls.exists() and calls.read_text().splitlines()[0] == "install --frozen-lockfile"
    assert os.path.isdir(info.worktree_path)

    py_repo = _repo(tmp_path / "python-repo", {"app.py": "pass\n"})
    py_calls = tmp_path / "py-calls.log"
    monkeypatch.setenv("FAKE_CALL_LOG", str(py_calls))
    WorktreeManager(repo_root=str(py_repo), worktrees_dir=str(tmp_path / "py-worktrees")).create_worktree("py")
    assert not py_calls.exists()


def test_worktree_install_disabled_and_install_error_keeps_worktree(tmp_path, monkeypatch):
    calls = _fake_path(tmp_path, monkeypatch, manager_exit=0)
    repo = _repo(tmp_path / "repo", {"package.json": "{}", "pnpm-lock.yaml": "lock"})
    (repo / "meister.config.yaml").write_text("environment: {install_dependencies: false}\n")
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "disabled"))
    info = manager.create_worktree("disabled")
    assert info and not calls.exists()

    broken_bin = tmp_path / "broken"
    _fake_executable(broken_bin, "pnpm", "import sys\nsys.exit(17)")
    (repo / "meister.config.yaml").write_text("")
    monkeypatch.setenv("PATH", str(broken_bin) + os.pathsep + os.environ.get("PATH", ""))
    info = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "failed")).create_worktree("failed")
    assert os.path.isdir(info.worktree_path)


def test_plan_import_globs_and_generated_file_warnings(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text(
        """### Task 1: Generate schema
Run `pnpm add drizzle-orm` and `drizzle-kit generate`.
**Files:**
- Modify: `package.json`
"""
    )
    result = CliRunner().invoke(main, ["plan", "import", str(plan)])
    assert result.exit_code == 0, result.output
    assert "Generate schema" in result.stderr
    assert "drizzle-kit generate" in result.stderr and "drizzle/*" in result.stderr
    assert "pnpm add/install" not in result.stderr

    plan.write_text(
        """### Task 1: Make migration
**Files:**
- Create: `drizzle/*`
"""
    )
    result = CliRunner().invoke(main, ["plan", "import", str(plan)])
    assert result.exit_code == 0, result.output
    assert "drizzle/*" in result.output

    plan.write_text(
        """### Task 1: Bad pattern
**Files:**
- Modify: `src/[broken`
"""
    )
    result = CliRunner().invoke(main, ["plan", "import", str(plan)])
    assert result.exit_code != 0
    assert "invalid glob" in result.stderr

    plan.write_text(
        """### Task 1: Dynamic routes
**Files:**
- Modify: `src/app/leads/[id]/page.tsx`
- Modify: `src/app/[...rest]/page.tsx`
- Modify: `src/app/[[...opt]]/page.tsx`
"""
    )
    result = CliRunner().invoke(main, ["plan", "import", str(plan)])
    assert result.exit_code == 0, result.output
    assert "invalid glob" not in result.stderr


def test_node_installer_uses_yarn_and_unlocked_package_manager(tmp_path, monkeypatch):
    log = _fake_path(tmp_path, monkeypatch)
    project = tmp_path / "node"
    project.mkdir()
    (project / "package.json").write_text('{"scripts":{"test":"jest"}}')
    (project / "yarn.lock").write_text("lock")
    config = tmp_path / "config.yaml"
    config.write_text(
        "environment: {install_dependencies: true}\n"
        "gate:\n  commands: [{name: check, run: 'python -c \"print(1)\"', timeout_seconds: 5, required: true}]\n"
    )
    assert DeterministicGate(str(project), load_config(str(config))).run_verification_ex().passed
    assert log.read_text().splitlines()[0] == "install --frozen-lockfile"

    log.unlink()
    (project / "yarn.lock").unlink()
    (project / "package.json").write_text('{"packageManager":"pnpm@9.0.0"}')
    assert DeterministicGate(str(project), load_config(str(config))).run_verification_ex().passed
    assert log.read_text().splitlines()[0] == "install"


def test_failed_worktree_setup_logs_and_keeps_worktree(tmp_path, monkeypatch):
    fake_bin = tmp_path / "bin"
    _fake_executable(fake_bin, "pnpm", "import sys\nsys.exit(17)")
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ.get("PATH", ""))
    repo = _repo(tmp_path / "repo", {"package.json": "{}", "pnpm-lock.yaml": "lock"})
    events = []
    monkeypatch.setattr("meister.logger.log_event", lambda **kwargs: events.append(kwargs))
    info = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "wts")).create_worktree("setup-fail")
    assert os.path.isdir(info.worktree_path)
    assert events and events[-1]["event_type"] == "worktree_setup_failed"
    assert "rc=17" in events[-1]["error"]


def test_pipeline_gate_infrastructure_preserves_worker_work_and_logs(tmp_path, monkeypatch):
    repo = _repo(
        tmp_path / "repo",
        {
            "app.py": "base\n",
            "meister.config.yaml": (
                "environment: {install_dependencies: false}\n"
                "gate:\n"
                "  commands: [{name: unit, run: 'missing-gate-command-xyz', "
                "timeout_seconds: 5, required: true}]\n"
            ),
        },
    )
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "wts"))
    events = []
    monkeypatch.setattr("meister.logger.log_event", lambda **kwargs: events.append(kwargs))
    pipeline = IntegrationPipeline(manager, gate=DeterministicGate(str(repo)))
    integration = pipeline.start_integration("infra")
    worker = manager.create_worktree("worker", base_ref=integration.branch_name)
    with open(os.path.join(worker.worktree_path, "app.py"), "a") as output:
        output.write("worker change\n")

    ok, message = pipeline.integrate_subtask(worker, target_files=["app.py"])
    assert not ok and message.startswith("ERRO DE INFRAESTRUTURA no portão:")
    assert "corrija o ambiente" in message
    assert "meister/worktree/worker" in _git(repo, "branch", "--list", "meister/worktree/worker").stdout
    preserved_sha = _git(repo, "rev-parse", "meister/worktree/worker").stdout.strip()
    assert preserved_sha != worker.base_commit
    assert _git(repo, "show", f"{preserved_sha}:app.py").stdout.endswith("worker change\n")
    assert any(event["event_type"] == "gate_infrastructure_error" for event in events)


def test_node_gate_runs_local_vitest_without_npx(tmp_path, monkeypatch):
    log = _fake_path(tmp_path, monkeypatch)
    project = tmp_path / "node"
    project.mkdir()
    (project / "package.json").write_text('{"devDependencies":{"vitest":"1"}}')
    (project / "pnpm-lock.yaml").write_text("lock")
    (project / "vitest.config.ts").write_text("export default {}\n")
    result = DeterministicGate(str(project)).run_verification_ex()
    assert result.passed and not result.infrastructure_error
    calls = log.read_text().splitlines()
    assert "local-vitest" in calls
    assert all("npx" not in call for call in calls)


def test_pipeline_gate_infrastructure_error_at_integration_stage(tmp_path, monkeypatch):
    from meister.gate import VerificationResult

    repo = _repo(tmp_path / "repo", {"app.py": "base\n"})
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "wts"))
    monkeypatch.setattr("meister.logger.log_event", lambda **kwargs: None)

    class StagedGate:
        def __init__(self):
            self.calls = 0

        def run_verification_ex(self, repo_path=None):
            self.calls += 1
            if self.calls == 1:
                return VerificationResult(True, "ok")
            return VerificationResult(False, "pnpm indisponivel", infrastructure_error=True)

    pipeline = IntegrationPipeline(manager, gate=StagedGate())
    integration = pipeline.start_integration("infra-int")
    worker = manager.create_worktree("worker-int", base_ref=integration.branch_name)
    with open(os.path.join(worker.worktree_path, "app.py"), "a") as output:
        output.write("worker change\n")

    ok, message = pipeline.integrate_subtask(worker, target_files=["app.py"])
    assert not ok
    assert message.startswith("ERRO DE INFRAESTRUTURA no portão:")
    assert "reprovado" in message
