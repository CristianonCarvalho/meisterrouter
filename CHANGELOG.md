# Changelog

This changelog follows the [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) format. The project uses semantic versioning and remains at `0.x` until the contract (JSON plan, `meister.config.yaml`, and log events) stabilizes. The `#N` numbers are pull requests.

## [Unreleased]

### Changed (incompatible)
- **`meister dashboard --host` outside loopback requires `--allow-remote`.** The dashboard has no authentication, and before this any address was accepted silently. Without the flag the command now exits with an error; with it, it prints a warning and starts. Loopback (`127.0.0.1`, `::1`, `localhost`) is unchanged. Anyone who exposes the dashboard on the network must add `--allow-remote`.
- **Minimum Python is 3.10.** The declared floor was 3.9, which was never tested; `requires-python`, `meister setup` and the CI matrix now all use 3.10, the version CI and mypy already assumed.
- **Lanes have neutral names.** Names that embedded a model or harness are replaced; update `workers.tier_order`, `meister.config.yaml`, `--model` arguments and any script that uses them. The old names no longer exist in the default configuration.

  | Old name | New name |
  |---|---|
  | `copilot_luna` | `tier_1b` |
  | `codex_luna` | `tier_1c` |
  | `agy_gemini_flash` | `tier_2` |
  | `claude_sonnet` | `tier_3` |

  The new `tier_1` lane (GitHub Copilot CLI with Claude Haiku 5.5) is the first choice by default. Model, harness and price stay in configuration (`meister/default_config.yaml` and `meister.config.yaml`), not in the lane name.

### Added
- **`meister wait [--run-id ID] [--timeout S] [--format text|json]`** blocks until a run ends and exits with a code that says how: `0` completed, `1` failed, `130` interrupted, `124` timeout, `2` usage error. It only reads the log (never writes), follows resumed runs and lets an orchestrator agent be notified without polling. See [docs/advanced.md](docs/advanced.md).
- **Page on the data sent to Jev:** [docs/jev-data-sent.md](docs/jev-data-sent.md) lists, field by field, what `classify` and `control` send to OpenRouter and what never leaves the machine (workers run through the local harnesses). The contract is locked by `tests/test_jev_payload_contract.py`: changing a field requires updating the page.
- **Web timeline (`/timeline`) readability:** time axis in minutes, right-angle dependency arrows, tooltips with phase, task and wait durations, lane header with `harness · model (effort)`, and a completion/failure sound with a button and the `s` key (off by default, preference saved per browser).
- **`effort` per lane:** an optional reasoning level passed to the harness (values depend on the harness: `copilot` and `github-copilot` accept none, minimal, low, medium, high, xhigh, max). When omitted, the harness argv is unchanged from before.
- **`workers.enabled`:** turns lanes on or off in the configuration, written safely, without ever leaving the configuration with no lane enabled.
- **`meister models --enable` / `--disable`:** turns a lane on or off from the command line; the `models` table shows a column with each lane's effort.
- **Claude Haiku 5.5 on lane `tier_1`** (GitHub Copilot CLI harness) as the default first lane.

### Fixed
- **Resuming a run now continues the attempt numbering instead of restarting at 1.** `_execute_subtask_core` started every call with `attempt_count = 0`, so a task resumed under the same `run_id` reused attempt numbers (events, per-attempt files such as `<run>_<task>_<N>_task.json`, `.json` and `.exit`) and mixed old and new attempts in the timeline and reports. It now starts from the attempts already stored for the subtask (`subtasks.attempts`). Verified with a real interrupt and `--resume`: `worker_spawn` attempts 1 and 2.
- **The bridge now notices within a second when a worker's `run-task` process dies without writing a result.** The command typed into the worker pane is wrapped in `/bin/sh -c` so the shell records the exit code in a `.exit` file even if Python crashes or fails to import; the bridge then fails the attempt (event `worker_process_exited`, retry with `reason=process_exit` and the exit code, then failure without lane escalation) instead of waiting for the 600 s idle timeout. Measured with a real `kill -9`: detection in 0.6 to 0.7 s. The progress line now says "worker process exited with code N" and "gate repair" instead of the misleading "pane lost; retry".
- **Interrupting `meister orchestrate` no longer leaves the run "running" forever.** Ctrl+C, `SIGTERM` or `SIGHUP` now record an `orchestration_end` event (`status: interrupted`, exit code 130), mark the run `FAILED` (resumable with `meister orchestrate --resume`), keep the worktrees and branches, print which run was interrupted and exit with code 130. Before this, the run stayed `RUNNING` in the state database and the timeline kept drawing it as running.
- **A harness that exits immediately no longer hides its real error on macOS.** `HarnessWorker.run` called `os.getpgid` right after launching the harness; on macOS that raises `ProcessLookupError` when the harness has already exited, so the task failed with `[Errno 3] No such process` instead of the harness's own message and exit code. The process group is now the PID (the harness is a session leader) and recording the PID file never aborts the task.
- `test_start_server_port_0_allocates_port_and_cleans_up` no longer fails on slow runners: longer wait and a reset of the idle watchdog clock that earlier tests could leave stale.
- **Integration no longer discards loose work.** `rollback_merge`, reuse of an integration worktree (registered or not) and the `branch -D` retry in `create_worktree` now archive loose changes and unmerged branch commits (`refs/meister/archive/*`) before any destructive git command (`reset --hard`, `clean -fd`, `branch -D`, `rmtree`). If archiving fails the step does not run; a blocked rollback now fails the subtask with an `integration` error instead of being reported as a gate failure. Covered by `tests/test_worktree_integration_data_loss.py`.
- **`current_run.json` is written atomically**, so an interruption no longer leaves a truncated file that loses the run correlation.
- Cleanup failures are no longer silent: they log a `warning`, emit a `cleanup_failure` event and appear as "Cleanup failures" in `meister report`.
- Web timeline: an attempt still open when a worker is respawned now ends at the new spawn, instead of stretching to the end of the run.
- A worker that fails at startup on a configuration error (e.g. `Unknown lane`) now writes the error result and logs `worker_task_error` immediately, instead of waiting for the 600 s idle timeout and escalating to another lane.
- The Jev now receives opaque lane keys (`lane_a`, `lane_b`...) instead of lane names: names like `tier_N` made it read a ranking and pick a more expensive lane. Its answer is translated back to the real lane name, so `recommended_implementer`, `fallback_chain` and the telemetry keep the real names.

### Other changes
- Tolerated scope changes emit `scope_tolerated` events and progress lines; this repository tolerates `tests/**` but still requires the full test suite to pass. Its `meister.config.yaml` uses `router.mode: first` (no Jev; `tier_order` fallback remains active).
- Configurable language foundation (`en` default, `pt-BR`), pipeline failure reason codes, and i18n string ratchet.
- Deterministic gate repair loop (`gate.repair_attempts`) and diagnostics preservation in subtask rejection events.
- Harness process-group cleanup on worker termination, with PID-identity-checked orphan reaping before retries.
- Fix: an idle or runtime timeout no longer fails the subtask with `worker_error` when the error result was written by our own harness cleanup; the timeout path (retry, escalation or salvage) takes precedence.
- Per-project timeline app window with `dashboard:` configuration and `orchestrate --no-open`; resumed runs are shown as in progress after a new `orchestration_start`.

## [0.9.1] - 2026-10-07

Hardening, installation and documentation release after 0.9.0 (PRs #78 to #87).

### Added
- **Optional light gate for documentation-only changes:** `gate.docs_only` (off by default). When every changed file is documentation, the pre-merge and post-merge gates run `gate.docs_only.commands` instead of the full suite (measured: 1.8 s versus 47.2 s); the final integration gate always runs the full suite (#87).
- **Version-pinned installer:** `install.sh --version vN.N.N|latest|main` (or `MEISTER_VERSION`), for example `curl ... | bash -s -- --version v0.9.1` (#80).
- **Herdr plugin entry script** (`bin/herdr-meister.sh`): finds the `meister` CLI (`PATH`, `~/.local/bin`, Homebrew) and, when it is missing, shows an install notification (exit code 127; a silent exit 0 at startup) instead of failing silently (#83).
- **Documentation in English:** `README.md`, everything under `docs/` and this changelog are now in English; the Portuguese versions live in `README.pt-BR.md`, `docs/pt-BR/` and `CHANGELOG.pt-BR.md`. A test keeps relative links and anchors valid in both languages (#82, #85).
- **Direct-usage guide, plan format page and `curl` installation** (#79).
- **MIT license**; the repository is now public (#78).

### Changed
- The plugin manifest no longer declares `[[keys.command]]` (Herdr does not read them); the shortcuts are written by `meister setup` (#81).

### Fixed
- `install.sh`: only the `/_npx/` and `/node_modules/` path components mark an npx install (a directory such as `~/my_npxproject` was misdetected), and returning from a pinned tag to `main` no longer fails (#80, #82).
- Test stability: the gate-cache timeout test (0.1 s to 1 s) and the bridge `max_runtime=0` test no longer fail under load (#84, #86).

## [0.9.0] - 2026-10-07

First tagged release. Consolidates the history of PRs #1 through #73 (2026-09-29 to 2026-10-07).

### Added
- **Orchestration:** running plans as a DAG with a worktree per subtask, `--no-ff` merge into an integration branch, and fast-forward of `main` only at the end (#1, #4, #6); canonical plan contract with a superpowers adapter (#5); `--resume` reuses completed tasks (#24); retry on the same lane when the pane disappears (#26); progress per task and final summary (#28); `meister plan analyze` and serial-plan warning (#46); inactivity timeout with work preserved (#32).
- **Routing:** Jev chooses the initial lane for each subtask and is the default mode, with protection against a slow Jev (#13, #17); structured context for Jev (#33); `max_parallel` per lane (#16); `eligible_classes` per lane, a deterministic rule governing Jev's choice (#63).
- **Configuration:** model catalog 100% in configuration, with packaged defaults (#14, #15); lane validation and display, lanes can be disabled, Copilot as the first lane (#12, #19).
- **Gate:** glob scope, Node environment setup, and configurable gate (#22); project venv, `{python}`, and `ok_exit_codes` (#49); pre-merge gate outside the integration lock (#47); gate cache by file tree (#66); tests in parallel with `pytest-xdist` (#64).
- **Cost and measurement:** actual worker usage and cost, and time per phase (#43); `meister report` (#44); Copilot cost by credits (`credit_usd`) (#51); updated model prices (#50).
- **Observability:** grouped, analytical dashboard with project, run, readable tasks, cost, overhead, and peak worker count (#35, #39, #41, #45, #53, #54); Gantt timeline in TUI, with live mode, overview of all runs, Jev lane, and `sem sinal` (no signal) state (#55, #56, #57, #58, #62); support for tasks run by `meister worker` (#73).
- **Operations:** `meister clean` safely removes old branches (#34); safe `meister init` (#20); Claude Code guard with `block`/`ask`/`off` modes and the "execution method = Meister" rule (#68, #69); execution manual (#25, #29); Mermaid diagrams (#72) and end-to-end flow (#18).
- **Automatic setup:** `meister setup` connects the plugin to Herdr, writes shortcuts to a managed block in `config.toml` (with backup and validation), checks the environment, and equips the project with `--project`; `meister config init` generates `meister.config.yaml` with the lanes and their order; `bin/install.sh` requires Herdr and calls `setup` (#76).
- **`meister --version`**, single source of version in `meister/__init__.py` and this changelog (#74).
- **Documentation:** concise README (MeisterRouter is a Herdr plugin, and Herdr is a prerequisite), timeline and dashboard screenshots (#75), updated Mermaid diagrams and the execution manual.

### Fixed
- Integration: worker commits made in the worktree itself are integrated (#4); the integration branch is rebuilt by merging (#6); rejected work is never lost, including uncommitted changes (#60).
- Detection of disappeared panes using the actual Herdr event format (#10), and lanes with an open circuit breaker are skipped (#7).
- Tasks without `target_files` run in isolation (#9); Next.js `[id]` routes and `--resume` for the same run (#31); `Global Constraints` no longer leak into every task (#23); the superpowers importer ignores titles in code blocks (#48).
- `npm install` does not run when there is nothing to install (#61); `prunable` worktrees no longer protect the branch (#37).
- Dashboard: readable table, Jev calls linked to tasks, sorting, and cost using the current catalog (#38, #40, #42, #52).

### Tests and CI
- Failure matrix with 13 failure points and E2E scripts with real tools (#2, #3, #8, #11, #30); per-test watchdog and guard against branch leaks (#36); clock-independent tests stabilized under load (#21, #27, #59, #65, #67).
