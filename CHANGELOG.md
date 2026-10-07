# Changelog

This changelog follows the [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) format. The project uses semantic versioning and remains at `0.x` until the contract (JSON plan, `meister.config.yaml`, and log events) stabilizes. The `#N` numbers are pull requests.

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
- **Version-pinned installer:** `install.sh --version vN.N.N|latest|main` (or `MEISTER_VERSION`), for example `curl ... | bash -s -- --version v0.9.0`.
- **`meister --version`**, single source of version in `meister/__init__.py` and this changelog (#74).
- **English README** (with Portuguese in `README.pt-BR.md`) for publication in the Herdr plugin marketplace; notice that the plugin alone does not install the `meister` CLI; manifest without shortcuts that Herdr does not read.
- **Herdr plugin:** entry wrapper (`bin/herdr-meister.sh`) in the `herdr-plugin.toml` manifest to locate the `meister` CLI (`PATH`, `~/.local/bin`, Homebrew) and display an installation notification with exit code 127 when missing (and silent exit code 0 at startup).
- **Documentation:** direct usage (the orchestrator LLM uses Meister according to the rules and hooks), prerequisites (Herdr required, Superpowers recommended), [plan format](docs/plan-format.md) page, and `curl` installation (public repository); concise README (MeisterRouter is a Herdr plugin, and Herdr is a prerequisite), with the [Alternative installation and advanced commands](docs/advanced.md) page, timeline and dashboard screenshots (#75), and updated diagrams.

### Fixed
- Integration: worker commits made in the worktree itself are integrated (#4); the integration branch is rebuilt by merging (#6); rejected work is never lost, including uncommitted changes (#60).
- Detection of disappeared panes using the actual Herdr event format (#10), and lanes with an open circuit breaker are skipped (#7).
- Tasks without `target_files` run in isolation (#9); Next.js `[id]` routes and `--resume` for the same run (#31); `Global Constraints` no longer leak into every task (#23); the superpowers importer ignores titles in code blocks (#48).
- `npm install` does not run when there is nothing to install (#61); `prunable` worktrees no longer protect the branch (#37).
- Dashboard: readable table, Jev calls linked to tasks, sorting, and cost using the current catalog (#38, #40, #42, #52).

### Tests and CI
- Failure matrix with 13 failure points and E2E scripts with real tools (#2, #3, #8, #11, #30); per-test watchdog and guard against branch leaks (#36); clock-independent tests stabilized under load (#21, #27, #59, #65, #67).
