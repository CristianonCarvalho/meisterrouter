# Changelog

This changelog follows the [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) format. The project uses semantic versioning and remains at `0.x` until the contract (JSON plan, `meister.config.yaml`, and log events) stabilizes. The `#N` numbers are pull requests.

## [Unreleased]

- Configurable language foundation (`en` default, `pt-BR`), pipeline failure reason codes, and i18n string ratchet.
- Deterministic gate repair loop (`gate.repair_attempts`) and diagnostics preservation in subtask rejection events.
- Harness process-group cleanup on worker termination, with PID-identity-checked orphan reaping before retries.
- Fix: an idle or runtime timeout no longer fails the subtask with `worker_error` when the error result was written by our own harness cleanup; the timeout path (retry, escalation or salvage) takes precedence.

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
