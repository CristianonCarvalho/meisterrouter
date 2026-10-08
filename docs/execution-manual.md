# MeisterRouter Execution Manual

For people using MeisterRouter in a project. Updated on 2026-10-02 (main through PR #28). Items marked **ACTION** require you to do something; items marked **(temporary)** are no longer needed once the indicated fix is merged, and the line can then be removed. The flow diagram is in [Flow](flow.md).

## Versions and releases
We follow SemVer; we use `0.x` until the contract stabilizes.
The version is changed in `meister/__init__.py` and in the `package.json` and `herdr-plugin.toml` literals, which the synchronization test verifies.
Each release gets the `vX.Y.Z` tag and an entry in `CHANGELOG.md`.
`meister --version` shows the installed version.

## 1. Once per machine
- **Installation:** `meister` is already installed (editable mode from the repository). On another machine: `git clone`, `python3 -m venv .venv && .venv/bin/pip install -e .`, `herdr plugin link <repo folder>`.
- **ACTION: OpenRouter key** (for Jev): `~/.meister/.env` with `OPENROUTER_API_KEY=...`. Today Jev happens to find it in the MeisterRouter repository's `.env`; on another machine, without that file, Jev is unavailable and routing falls back to the first lane (with a warning).
- **ACTION: worker CLIs** installed and logged in: `copilot` (primary lane), `agy`, `claude`; `codex` only if you plan to use it.
- **ACTION: keep the MeisterRouter repository on the `main` branch.** The Herdr plugin runs the code in the folder; on a work branch, you run unmerged code. To update: `git checkout main && git pull`.
- Herdr plugin: `herdr plugin list` should show `dev.meisterrouter.orchestrator` pointing to the repository folder. The daemon starts with Herdr; restart it with `meister daemon --stop` and `meister daemon --start` if you update the code.

### Installation and automatic shortcuts
`meister setup` automates linking the plugin in Herdr, diagnosing the environment, and configuring shortcuts in `~/.config/herdr/config.toml`. The default shortcuts (`prefix+m`, `prefix+shift+m`, `prefix+t`) are inserted in a delimited managed block; `--direct-keys` includes the equivalent `ctrl+alt+*`. Before writing, it creates a timestamped backup and validates with `herdr config check`; existing user shortcuts are never overwritten (conflicts are reported as a warning). Use `--dry-run` to simulate without changing files or running state-changing commands, and `--project [DIR]` to initialize rules and hooks in a project at the same time. To start configuring a project with the default lanes commented out, use `meister config init` (generates `meister.config.yaml` and refuses to overwrite without `--force`).

## 2. Per project
- **ACTION: the project must be a git repository with at least one commit** (workers use worktrees and integration finishes on `main`).
- **Starter files:** `meister init` (from the project root) creates `AGENTS.md`, `CLAUDE.md`, and `CODEX.md` without fixed templates and **never overwrites** existing files (`--force` to overwrite). Hooks are optional (`--hooks`).
- **Configuration (optional):** `meister.config.yaml` at the root overrides `meister/default_config.yaml`. Check with `meister config show` and `meister config validate`. The `workers.tier_order` list in your file **replaces** the default; the other sections are merged. Commented examples are in `meister.config.example.yaml`.
- Each lane accepts `eligible_classes` (`SMALL`, `MEDIUM`, `HIGH`, `ESCALATE`); an empty list (the default) means unrestricted. In the default catalog, `claude_sonnet` is only eligible for `ESCALATE`.
- With `router.mode: jev`, Jev recommends a lane, but this deterministic rule corrects an incompatible recommendation to the nearest eligible lane in order. Eligibility applies only to the initial selection, not to fallbacks.
- **Codex is disabled** (`codex_luna`, limited credits). To use it in automatic routing, enable it in your `meister.config.yaml` (`enabled: true`); for a one-off use, `meister worker --model codex_luna --task "..."`.
- **Node/TypeScript project:** nothing to do. MeisterRouter installs dependencies in each worktree (`pnpm install --frozen-lockfile`, `npm ci`, or `yarn install --frozen-lockfile`, depending on the lockfile), and the gate uses local binaries (`node_modules/.bin`), without `npx`. To disable: `environment.install_dependencies: false`.
- **Python project:** create `.venv` in the root once (`python3 -m venv .venv`); the gate also detects it in worktrees. Example:
  ```yaml
  gate:
    commands:
      - {name: ruff,   run: "ruff check app tests", timeout_seconds: 120, required: true}
      - {name: mypy,   run: "mypy app",             timeout_seconds: 180, required: true}
      - {name: pytest, run: "{python} -m pytest -q", ok_exit_codes: [0, 5], timeout_seconds: 300, required: true}
  ```
  Commit the scaffold (`app/__init__.py` and a minimal test) before tasks; without them `mypy app` fails because the directory does not exist. Tasks that change `requirements*.txt` do not reinstall the venv.
- **Gate cache:** within the same run, an approval is reused when the Git tree and gate commands/configuration are identical. Any change to a non-ignored file, commands, configured Python, or installation invalidates the cache; failures and infrastructure errors are never cached. Disable with `gate.cache: false`. Files ignored by `.gitignore` and state external to the repository (for example, services or environment variables) are not included in the key.
- **Project that is not Python/Node/Rust** (Go, Java...): declare the gate in `meister.config.yaml`, for example `gate.commands: [{name: unit, run: "go test ./...", timeout_seconds: 300, required: true}]` (no shell). Without this, the gate does not detect tests and fails; `gate.allow_unverified: true` accepts without verification (not recommended).
- **Generated files** (lockfiles, `*.tsbuildinfo`, `.vitest/**`) are already tolerated in scope; add others to `scope.tolerated_files` (the list **replaces** the default: copy the default and add yours).
- Worker context: if you like, write the stack, test command, and conventions in the **project's** `AGENTS.md`.

### Claude Code guard
The optional guard installed with `meister install-hooks --claude` protects code edits.
The default mode is `block`; configure it in `.meister/guard_mode` with `echo ask > .meister/guard_mode`.
`ask` requests confirmation for each code edit; `echo off > .meister/guard_mode` disables the guard.
If the file is missing, empty, or has an unknown mode, the safe `block` behavior remains in effect.
Documentation (`docs/**`, `*.md`, `*.mdx`, `*.txt`) passes without asking, in any mode.
`MEISTER_IN_PANE=1` permits workers; `MEISTER_ALLOW_ORCHESTRATOR_EDIT=1` also permits edits.
The `.meister/allow_orchestrator` file is another explicit bypass for direct edits.
Always use `meister orchestrate` to implement tasks and plans (`meister worker` for an isolated task).
Do not offer native execution or alternative workflows; if Meister is unavailable, ask the user.

## 3. Run a plan
1. In Herdr (workers open in `worker:*` tabs):
   `meister plan import --format superpowers plan.md -o plan.json` and `meister plan validate plan.json`
2. Before running, use `meister plan analyze plan.json` to see batches, dependencies, and file conflicts. Per-file estimates ignore semantic dependencies; `--max-workers N` changes the ceiling used in estimated steps.
3. `meister orchestrate --plan-file plan.json`
4. **ACTION: do not pipe the output** (`| head`, `| tee` without `pipefail`): the exit code you see becomes the pipe's, not the orchestrator's. Failure = non-zero code.
5. Superpowers plans chain tasks by default (`--deps sequential`). For real parallelism, use an explicit `**Depends on:**` or `--deps files`. Tasks without `target_files` run in isolation, one at a time.
6. `**Files:**` accepts globs (`src/text/*.ts`, `drizzle/*`). Declare all files the task may create or change; anything outside scope fails the task.
7. At the end, the result is integrated into the project's `main` by fast-forward; nothing is included without passing the gates.

### Timeline window
At the start of `meister orchestrate`, MeisterRouter starts the project's local timeline server and opens the timeline in a browser. It shows the same live task timeline as `meister timeline`, in a browser window.

Configure this in `meister.config.yaml`:
```yaml
dashboard:
  open: auto # auto | app | tab | never
  idle_exit_minutes: 30
```
`auto` (the default) and `app` try to open a Chromium app window, falling back to a tab in the default browser; `tab` opens a default-browser tab; `never` disables the timeline server and browser launch. `meister orchestrate --no-open` has the same effect for that invocation. There is at most one app window per project: later orchestrations reuse that project's server and window. Each project has its own port, URL, and `.meister/dashboard.json` state file.

The server listens only on `127.0.0.1`. Without a graphical display (for example, over SSH or in CI), MeisterRouter does not try to open a browser and prints the timeline URL so you can open it yourself later. If Chromium is unavailable, it tries the default browser tab instead. A server or browser-launch failure does not stop orchestration. The server exits by itself after `dashboard.idle_exit_minutes` without use, once no run is active; to stop a forgotten server sooner, run `kill <PID>` using the PID recorded in `.meister/dashboard.json`.

## 4. When something fails: what you need to do
`meister orchestrate` shows progress by task and the final summary on stderr; the success/failure message
remains on stdout. Use `--quiet` (or `-q`) to omit progress and the summary.

| Situation | What to do |
|---|---|
| Run failed or was interrupted (notebook closed, worker tab closed, outage) | **Run THE SAME command again**: completed tasks are skipped. A closed worker tab is detected in ~5 s. |
| You **edited the plan** (e.g., added a file in `Files:`) after completed tasks | Run `meister orchestrate --plan-file plan.json --resume` (or `--resume <run_id>`). Completed, integrated, unchanged tasks (same description and dependencies, existing commit, within the new scope) are reused; the others run again. Without `--resume`, this is a new run and everything is redone (the command warns when a reusable run exists). |
| `Scope violation` | The message indicates the file. Declare it in `**Files:**` (accepts globs) or in `scope.tolerated_files`, edit the plan, and run with `--resume`. |
| `INFRASTRUCTURE ERROR in gate` (installation, local binary missing, timeout) | The worker's code **was not rejected** and its work was preserved in a commit. Fix the environment (e.g., run `pnpm install` in the project, check `pnpm`/`node` in PATH) and run the same command. |
| You **closed the Herdr window** (or detached with `ctrl+b q`) | Nothing. Herdr keeps a server running in the background: panes, workers, and `meister orchestrate` keep running. Reopen with `herdr`. |
| You **turned off the computer** or stopped the server (`herdr server stop`) | The run dies with it (`orchestrate` is a regular process and Herdr does not preserve processes, only the layout). Run the same command (or `--resume` if you edited the plan). Suspending the notebook (closing the lid) generally does not stop processes, but a network outage can affect workers. |
| A worker's **tab/pane disappeared** (closed manually or the agent failed) | Automatic: MeisterRouter retries **once**, on the same lane and in the same worktree (`retry.pane_lost_attempts`, `retry.pane_lost_backoff_seconds`; `0` disables). If the retry also fails, the run fails with "`retentativas esgotadas` (retries exhausted)"; run the same command. |
| Worker quota exhausted | Automatic: the lane's circuit breaker opens (60 s) and uses the next lane. If all are exhausted, the run fails; run it again later. |
| Jev unavailable (OpenRouter down or no key) | Automatic: uses the first lane for 5 min without retrying. Without a key, `meister config validate` warns. |

## 5. Where to look
- **Events:** JSONL in `~/.meister/logs` (or `MEISTER_LOG_DIR`); `meister replay` reconstructs the timeline. `meister dashboard` opens the task-grouped dashboard, with run selection, filterable/paginated event analysis, and an explicit indication of the file read. Use `meister dashboard --log-dir PATH` to point to another directory. The dashboard does not estimate savings without a measured baseline, and unrecorded worker costs appear as “`não medido` (not measured)”. Useful events: `subtask_reused`/`subtask_not_reused` (resume), `gate_infrastructure_error`, `worktree_setup_ok`/`worktree_setup_failed`.
- **Run report:** `meister report --run-id ID` shows tasks, cost, phases, and overhead; repeat `--run-id` to compare runs. Use `--group A=ID1,ID2` for the median/minimum/maximum of a group, `--format json|markdown` to export, or `--log-dir PATH` to read another `orchestration_log.jsonl`. Run prefixes must be at least 6 characters; missing metrics in old logs appear as “`não medido` (not measured)”. Copilot cost comes from credits × `credit_usd`.
- **Timeline (Gantt):** `meister timeline` opens a colored Gantt chart in the terminal (e.g., in a Herdr tab) for the newest run: one bar per task on the time axis, color by phase (worker, gate, integration, wait), the Jev band for decision calls, a pulsing endpoint on tasks in progress, and visible parallelism (overlapping tasks, peak, and average at the top); it also shows tasks run by `meister worker`. A run with no end and no events beyond `max_runtime_seconds` + 5 min appears as `⚠ SEM SINAL` (no signal). Keys: `a` toggles between one run and all stacked runs, `[`/`]` switch runs (replay), `l` returns to live, `p` pauses, `+`/`-` zoom, `←`/`→` time, `↑`/`↓` lines, `?` help, `q` quits; `meister timeline --all` opens directly on all runs. `--once` prints one frame and exits (useful for piping); `--no-color` or `NO_COLOR=1` disables colors; requires 80 columns. Inside `meister dashboard --tui`, the `t` key opens the same screen. It only reads the log; the current phase of an in-progress task is inferred (the log records the phase on completion), and there is no percentage per task.
- **State:** `.meister/meister.db` in the project (runs, subtasks, circuit breakers). A resumed run stores `resumed_from`; its source run receives `superseded_by`.
- **Rejected work is never lost:** commits and uncommitted changes remain in `refs/meister/archive/*` (the latter use refs with the `-uncommitted` suffix). Recover a diff with `git diff <base>..refs/meister/archive/<ref>` or a file with `git checkout <ref> -- <file>`; commits reused on resume are pinned in `refs/meister/resume/*`.
### Leftovers

After a failure or resume, use `meister clean` to simulate removing old `meister/integration/*` and `meister/worktree/*` branches; `meister clean --apply` applies safe removals. Unintegrated commits are kept by default; `--archive-and-delete` creates a ref in `refs/meister/archive/` before deleting them. Branches checked out in worktrees, the current branch, archive refs, and past runs passed with `--keep` are protected. The lock prevents applying while `orchestrate`, `run-task`, or `worker` is running (`--force-busy` ignores it). `--close-stale-runs` cancels runs without an existing process, panes, or integration branch. Do not use `git branch -D` manually to clean up MeisterRouter branches.

## 6. Maintenance reminder for this manual
For each PR that removes a line marked **(temporary)**, remove that line here. Update the date and PR at the top.
