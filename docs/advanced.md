# Alternative installation and advanced commands

This page gathers what **is not necessary for daily use**. The simple path (install Herdr,
run the installer, `meister setup --project`, and ask your orchestrator LLM to do the work) is in the
[README](../README.md).

- [Alternative installation](#alternative-installation)
- [Herdr shortcuts manually](#herdr-shortcuts-manually)
- [The OpenRouter key](#the-openrouter-key)
- [Set up a project (`init`, hooks, and guard)](#set-up-a-project-init-hooks-and-guard)
- [Commands `orchestrate` already runs for you](#commands-orchestrate-already-runs-for-you) (`classify`, `control`, `worker`)
- [Plan and execution manually (`plan`, `orchestrate`, `--resume`)](#plan-and-execution-manually-plan-orchestrate---resume)
- [Clean up old branches (`clean`)](#clean-up-old-branches-clean)
- [Dashboard and timeline: options](#dashboard-and-timeline-options)
- [Lane names](#lane-names)
- [Per-lane effort](#per-lane-effort)
- [Enabling and disabling lanes](#enabling-and-disabling-lanes)
- [Advanced configuration](#advanced-configuration)
- [Development and testing](#development-and-testing)
- [Project structure](#project-structure)
- [GitHub Copilot CLI adapter](#github-copilot-cli-adapter)

---

## Alternative installation

The recommended installer (`bin/install.sh`) clones the repository to `~/.local/share/meisterrouter`, creates the
`.venv`, installs the package in editable mode, creates the `~/.local/bin/meister` executable, and runs `meister setup`.
These are the other ways to reach the same result.

### Manual clone (Git / SSH)
```bash
# Via HTTPS:
git clone https://github.com/CristianonCarvalho/meisterrouter.git
cd meisterrouter
./bin/install.sh

# Or via SSH:
git clone git@github.com:CristianonCarvalho/meisterrouter.git
cd meisterrouter
./bin/install.sh
```

### Pin a version (tag)
The recommended installer installs `main` by default. For a curl installation, pin a tag with `--version`:
```bash
curl -fsSL https://raw.githubusercontent.com/CristianonCarvalho/meisterrouter/main/bin/install.sh | bash -s -- --version v0.9.1
```
`latest` selects the highest available tag; `main` selects the latest code from the main branch:
```bash
curl -fsSL https://raw.githubusercontent.com/CristianonCarvalho/meisterrouter/main/bin/install.sh | bash -s -- --version latest
curl -fsSL https://raw.githubusercontent.com/CristianonCarvalho/meisterrouter/main/bin/install.sh | bash -s -- --version main
```
You can also set `MEISTER_VERSION=v0.9.1` in the environment; for example, in a piped installation:
```bash
curl -fsSL https://raw.githubusercontent.com/CristianonCarvalho/meisterrouter/main/bin/install.sh | MEISTER_VERSION=v0.9.1 bash
```
The `--version` argument takes precedence if both are used. If `~/.local/share/meisterrouter` already exists as a clone, a tag is fetched and selected in that clone; with `main`, the installer selects `main` and updates the branch. When run inside a local clone, that clone is not changed and a requested pinned version is ignored with a warning.
To clone the tag manually, use:
```bash
git clone --branch v0.9.1 https://github.com/CristianonCarvalho/meisterrouter.git
cd meisterrouter && ./bin/install.sh
meister --version     # meister 0.9.0 (commit ...)
```
Versions and release notes are available in [Releases](https://github.com/CristianonCarvalho/meisterrouter/releases) and the [`CHANGELOG.md`](../CHANGELOG.md).

### Via Node.js / NPM
```bash
# Install globally directly from GitHub:
npm install -g git+https://github.com/CristianonCarvalho/meisterrouter.git

# Or directly from the cloned directory:
npm install -g .
```
The Node.js runner in `bin/cli.js` manages the runtime and Python bootstrap.

### Manual installation with Python (pip / venv)
```bash
git clone https://github.com/CristianonCarvalho/meisterrouter.git
cd meisterrouter
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```
Then run `meister setup` to connect the plugin to Herdr and register the shortcuts.

### Connect the plugin to Herdr manually
```bash
herdr plugin link /caminho/para/meisterrouter      # or ~/.local/share/meisterrouter
herdr plugin action list                           # check the registered actions
```
The plugin (`dev.meisterrouter.orchestrator`) registers the `auto-orchestrate`, `classify-task`, and
`verify-gate` actions, and starts the daemon together with Herdr.

### The plugin without the CLI
If you installed only the plugin (for example, from the marketplace) or if the `meister` executable is not on Herdr's `PATH`, the entry script (`bin/herdr-meister.sh`) tries to locate it in `~/.local/bin`, `/opt/homebrew/bin`, and `/usr/local/bin`. If the CLI is not found, the plugin actions display a notification in Herdr with the installation command and exit with code 127. At startup (`startup` event), the guidance message is recorded in the plugin log and the process exits with code 0 (without a visual notification). To inspect the plugin logs in Herdr:
```bash
herdr plugin log list --plugin dev.meisterrouter.orchestrator
```

---

## Herdr shortcuts manually

`meister setup` writes the shortcuts in a managed block in `~/.config/herdr/config.toml`. If you prefer
to register them yourself, add them to the file (replace `meister` with the full path, for example
`~/.local/bin/meister`, if it is not on Herdr's `PATH`) and then run `herdr config check` and
`herdr server reload-config`:

```toml
[[keys.command]]
key = "prefix+m"                      # orchestrate
type = "shell"
command = "meister herdr-action orchestrate"

[[keys.command]]
key = "prefix+shift+m"                # dashboard (TUI) in a popup
type = "popup"
command = "meister dashboard --tui"
width = "85%"
height = "85%"

[[keys.command]]
key = "prefix+t"                      # timeline (Gantt) in a popup
type = "popup"
command = "meister timeline"
width = "85%"
height = "85%"
```

| Shortcut | What it does |
|---|---|
| `prefix+m` or `ctrl+alt+m` | Starts autonomous orchestration in the workspace |
| `prefix+shift+m` or `ctrl+alt+shift+m` | Opens the **dashboard** (TUI) in a popup |
| `prefix+t` or `ctrl+alt+t` | Opens the **timeline** (Gantt) in a popup |

The `ctrl+alt+...` versions work without using the `prefix`: repeat the blocks with the other key.
In the TUI dashboard, `t` opens the timeline and `q` closes the popup.

---

## The OpenRouter key

Only **Jev** uses OpenRouter (workers run on your subscriptions). Jev looks for the key in this order:
1. the `OPENROUTER_API_KEY` environment variable;
2. a `.env` file in the current directory;
3. `~/.meister/.env`;
4. the `.env` in the MeisterRouter repository.

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."          # or
echo 'OPENROUTER_API_KEY=sk-or-v1-...' >> ~/.meister/.env
```
Without a key, use `router: {mode: first}` in `meister.config.yaml`: the first lane is selected without network access.

---

## Set up a project (`init`, hooks, and guard)

`meister setup --project` already does this. The underlying command is:
```bash
meister init --target /caminho/do/seu/projeto [--hooks] [--force]
```
Creates the following in the project without overwriting existing files:
- `CLAUDE.md` (instructions for Claude Code), `CODEX.md` (OpenAI Codex), and `AGENTS.md` (Cursor, Copilot, and subagents);
- the `.meister/logs/` telemetry directory.

Hooks are optional and are installed only with `--hooks`. The Git `pre-commit` hook summarizes staged changes and
runs available tests (`npm test`, `pytest`, or `cargo test`); it can block the commit if they fail. Existing hooks that do not belong to MeisterRouter are preserved; `--force` overwrites them.
`--no-hooks` is still accepted for compatibility, with no effect.

`--hooks` also installs the **Claude Code** hooks in `.claude/`, including the **guard**: it prevents
Claude from editing code directly in the project, because the execution method is always `meister orchestrate` (or
`meister worker`). The mode is set in `.meister/guard_mode`:

| Mode | Effect |
|---|---|
| `block` (default) | refuses the edit and directs the user to use `meister orchestrate` |
| `ask` | asks for confirmation on every code edit |
| `off` | disables the guard |

Documentation (`docs/**`, `*.md`, `*.mdx`, `*.txt`) is always allowed, and Meister workers
(`MEISTER_IN_PANE=1`) are not affected. See [`execution-manual.md`](execution-manual.md) for details.

---

## Commands `orchestrate` already runs for you

You do not need these for daily use; they are for testing or debugging an isolated component.

### `classify`
`orchestrate` calls Jev for each subtask and selects the initial lane. Manually:
```bash
meister classify --context "Adicionar filtro de busca por data no painel"
```
Returns typed JSON with the complexity (`SMALL`, `MEDIUM`, `HIGH`, `ESCALATE`) and the recommended
implementer among the lanes configured in `tier_order`.

### `control`
`orchestrate` calls Jev at the end, before the `main` fast-forward. Manually:
```bash
meister control --diff-summary "Adicionado input e testes no componente Filter.tsx" --test-result pass
```
Returns the authorized action: `COMPLETE` (can commit and finish), `RETRY` (try again), or
`switch_implementer` (switch to the next model in the hierarchy).

### `worker`
`orchestrate` runs workers through internal functions; the `worker` command runs **one isolated task**, without a plan:
```bash
# Run using the selected lane:
meister worker --model tier_1 --task "Corrigir tooltip overflow" --files "src/components/SynastryPanel.tsx"

# If the model fails or is inactive, escalate to the next lane:
meister worker --model tier_1b --task "Corrigir tooltip overflow" --files "src/components/SynastryPanel.tsx"
```
> ⚠️ **No direct implementation by the orchestrator.** Architect models (Claude, Codex) must not write
> implementation code when a worker is configured. If the recommended lane fails, the rule is to
> cascade through the `workers.tier_order` fallback chain. The architect implements only if **all** lanes
> are proven inaccessible.

### Other supporting commands
```bash
meister report [--run-id ID] [--format table|json|markdown]   # cost, time, and attempts per run (read-only)
meister replay RUN_ID [--json]          # replay and deterministic audit of a run's events
meister config show | validate          # display and validate the active configuration
meister daemon --status                 # daemon status (starts automatically with Herdr)
meister install-hooks --target . --claude --git   # hooks only, without init
```

---

## Plan and execution manually (`plan`, `orchestrate`, `--resume`)

For direct use, your orchestrator LLM does this for you (see the [README](../README.md)). Manually, the flow is:
```bash
meister plan import plano.md -o plano.json     # Markdown (Superpowers format) -> canonical JSON
meister plan validate plano.json               # check schema, dependencies, and prohibited keys
meister plan analyze plano.json                # expected parallelism and serial-plan warning
meister orchestrate --plan-file plano.json     # execute; --quiet suppresses progress and summary
```
The Markdown format, `Files:`/`Depends on:` rules, and canonical JSON are in
[`plan-format.md`](plan-format.md). `plan import` accepts `--deps sequential|files` and `--allow-unscoped`.

`orchestrate` shows each task's progress and a final summary on stderr; the result phrase remains on
stdout. To reuse completed tasks from a previous run after editing the plan, use `--resume`
(selects the most recent eligible FAILED/RUNNING run in the same directory) or provide the identifier:
```bash
meister orchestrate --plan-file plano.json --resume
meister orchestrate --plan-file plano.json --resume 0123456789abcdef
```
Only tasks with unchanged descriptions and dependencies, an existing commit, and a compatible scope are resumed. Without
`--resume`, the entire plan runs and Meister notifies you when there is a previous run that can be reused.

It also accepts `--task '<text or plan JSON>'` and `-c <config.yaml>`; the legacy format (free text) requires
`--allow-freeform` and does not validate dependencies or schema. In Herdr, `prefix+m` starts orchestration.

---

## Clean up old branches (`clean`)

At each run, Meister already removes orphaned worktrees and tabs and deletes archive refs older than 7 days.
`meister clean` removes old `meister/integration/*` and `meister/worktree/*` branches from failed or abandoned
runs. Without `--apply`, it only shows what would be removed; it protects the
current branch, branches open in worktrees, and runs specified with `--keep`:

```bash
meister clean
meister clean --repo /caminho/do/projeto --apply
meister clean --apply --archive-and-delete --close-stale-runs
```
Merged branches, branches equivalent to the base, or already archived branches can be removed with `--apply`. Unmerged
commits are preserved unless `--archive-and-delete` first creates a ref in
`refs/meister/archive/`. The operation aborts with code 3 if it detects `meister orchestrate`, `run-task`, or
`worker` running; `--force-busy` ignores the merge lock. `--base BRANCH` selects the base, `--keep PREFIXO` protects
runs, and `--json` returns the structured result.

---

## Dashboard and timeline: options

### Web dashboard
```bash
meister dashboard                              # http://localhost:5050
meister dashboard --port 5077 --log-dir /caminho/para/logs
```
Selects the most recent run by default and reports the JSONL file it read; `--log-dir` points to another
log directory without changing `MEISTER_LOG_DIR`. Worker costs appear only when recorded; percentage
savings are not estimated without a measured baseline. Copilot cost comes from the **credits** reported by the
CLI (credits × `credit_usd`, 1 credit = US$ 0.01), not from the token estimate. In the all-runs view,
the table has a **Run** column (one color per run), with the newest runs first.

### TUI dashboard
```bash
meister dashboard --tui
```
In Herdr, the popup shortcut opens this screen over the layout; `q` closes it, `o` opens the web dashboard, and `t` opens the
timeline.

### Timeline (Gantt)
```bash
meister timeline                    # live: the newest run, updating automatically
meister timeline --once             # print one frame and exit
meister timeline --run-id 3f9a1c    # a specific run (6+ character prefix)
meister timeline --all              # all runs
meister timeline --no-color         # without colors
```
One bar per phase (worker, gate, integration) for each task, the actual lane and model, Jev's wait on its
own line, and the cost. Read-only: does not change the log. Keys: `[` and `]` older/newer run, `l`
returns to live, `a` one run/all, `p` pauses, `+` and `-` zoom, arrows scroll and move through time, `?` help, `q`
quits. Also shows tasks run by `meister worker`. A run without an end and with no events beyond
`max_runtime_seconds` + 5 min appears as `⚠ SEM SINAL` (no signal).

### Web timeline

The same timeline is also served in the browser by the dashboard, at `/timeline` (for example,
`http://localhost:5050/timeline` while `meister dashboard` is running). It is read-only and shows the same data as the terminal version.

- **Time axis in minutes:** the axis labels show the time elapsed since the start of the run (`+59s`, `+1m05s`), so long runs stay readable.
- **Elbow arrows:** dependencies between tasks are drawn as right-angle arrows: a horizontal stub, a vertical trunk per lane, and a horizontal tail into the dependent task.
- **Duration tooltips:** hovering over a bar shows the phase duration, the task's total duration, and the wait for Jev. Hovering over a lane label shows the lane details.
- **Lane header:** each lane shows `harness · model (effort)`, for example `copilot · <model> (H)`. Effort abbreviations: `N` none, `MIN` minimal, `L` low, `M` medium, `H` high, `XH` xhigh, `MAX` max. Other values appear in uppercase, and missing parts are omitted.
- **Sound:** a short tone plays when a task finishes (rising notes) or fails (falling notes). Toggle it with the **Sound on / Sound off** button or the `s` key. The choice is saved in the browser (`localStorage`) and applies only to that browser. Sound is **on** by default; browsers block audio until you interact with the page, so it starts after your first click or key press.
- **Open attempts:** when a worker is respawned, the previous attempt that was still open ends at the new spawn, instead of stretching to the end of the run.

The keys match the terminal timeline (`[`, `]`, `l`, `a`, `p`, `+`, `-`, arrows, `?`), plus `s` for sound. Press `?` on the page to see the table.

---

## Lane names

Each entry in `workers.tier_order` has a `name`. The name is what you pass to `meister worker --model`,
`meister models --enable/--disable`, `workers.enabled`, the timeline, and the reports.

The convention:
- `tier_N` is the primary lane of position N in the chain (`tier_1` for small edits, `tier_2` for deep reasoning,
  `tier_3` for escalation).
- `tier_Nb`, `tier_Nc`, ... are alternatives at the same position, on another harness or model.

The name describes the position in the chain, not the model. To see which harness, model, and cost each lane uses,
run `meister models`. The names used in earlier versions are listed in the [`CHANGELOG.md`](../CHANGELOG.md).

```bash
meister worker --model tier_1 --task "Corrigir tooltip overflow" --files "src/components/SynastryPanel.tsx"
meister worker --model tier_1b --task "Corrigir tooltip overflow" --files "src/components/SynastryPanel.tsx"
```

---

## Per-lane effort

A lane can set `effort`, the reasoning-effort level passed to its harness. It is optional:

```yaml
workers:
  tier_order:
    - {name: tier_2, harness: agy, model: <model>, effort: high, cost_per_m_tokens: 1.50, max_retries: 2}
```

- **Omitted** (the default): the harness argv is exactly what it was before this option existed, and the
  harness uses its own default.
- **Set**: Meister adds the harness flag shown below. Values are case-insensitive.
- **Invalid** (a value the harness does not accept, or `effort` on a harness that does not support it):
  `meister config validate` reports an error at `workers.tier_order[i].effort`.

| Harness | Accepted `effort` values | Flag added to the command |
|---|---|---|
| `copilot` | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` | `--reasoning-effort VALUE` |
| `claude` | `low`, `medium`, `high`, `xhigh`, `max` | `--effort VALUE` |
| `agy` | `low`, `medium`, `high`, `xhigh`, `max` | `--effort VALUE` |
| `codex` | `minimal`, `low`, `medium`, `high`, `xhigh` | `-c model_reasoning_effort="VALUE"` |

The architect has its own `architect.effort` setting; it is independent of the lanes.

---

## Enabling and disabling lanes

A lane is on or off. Lanes that are off stay in the configuration file and appear as disabled in `meister models`.

There are two ways to switch a lane:
- **In the file**: set `enabled: false` on the lane entry, or use the `workers.enabled` mapping, which overrides
  the entry's flag:
  ```yaml
  workers:
    enabled:
      tier_1c: true
      tier_3b: false
  ```
- **With the command**: `--enable` and `--disable` can each be repeated:
  ```bash
  meister models --enable tier_1c --disable tier_1
  meister models --config meister.config.yaml --disable tier_3
  ```
  The command writes the `workers.enabled` mapping in `meister.config.yaml` (or in the file given to `--config`).
  Before writing, it saves the previous file as `<file>.bak` and validates the configuration.

Rules:
- You cannot disable the last enabled lane; at least one lane must remain on.
- Unknown lane names are rejected, and the same lane cannot appear in both `--enable` and `--disable`.
- Changes take effect on the **next run**. A run already in progress keeps the configuration it started with.

---

## Advanced configuration

Defaults are in `meister/default_config.yaml`; the project's `meister.config.yaml` overrides them (lists
such as `tier_order` **replace** the entire list). See the README for selecting and ordering lanes; the rest is here.

**Language**
- `language`: `en` (default) or `pt-BR` in `meister.config.yaml`. Can also be defined via the `MEISTER_LANG` environment variable. Precedence: (1) explicit `--config` CLI option, (2) `MEISTER_LANG`, (3) `meister.config.yaml` in current working directory, (4) default `en`. In this version, messages are in the process of being translated (the existing Portuguese messages remain available).

**Router**
- `router.mode`: `jev` (default) or `first` (always the first lane, no network, deterministic offline operation).
- This repository's `meister.config.yaml` uses `router.mode: first`; Jev is not used, and the `tier_order` fallback remains active.
- `router.timeout_seconds` (10) limits each call to Jev; `router.max_attempts` (2) sets the attempts;
  `router.unavailable_cooldown_seconds` (300) sets how long new calls are avoided after a failure.
- `router.context_max_chars` (4000, minimum 500) limits the context sent to Jev: the task header and its
  files/dependencies are preserved, and the body is truncated as needed. Global project constraints are
  summarized, not sent to the classifier.
- `workers.tier_order[].eligible_classes`: classes (`SMALL`, `MEDIUM`, `HIGH`, `ESCALATE`) accepted by the lane. If
  Jev recommends an ineligible lane, Meister uses the nearest eligible lane (the previous one in the list, otherwise
  the next one, otherwise keeps the selection). By default, Sonnet receives only `ESCALATE`.
- `workers.tier_order[].credit_usd`: dollar price of one lane credit (Copilot: `0.01`), used for cost calculation.
- `workers.tier_order[].max_parallel`: how many subtasks start per lane; a later fallback does not transfer the slot.

**Retries and time**
- `retry.pane_lost_attempts` (1; `0` disables): additional retries on the same lane when a pane disappears or
  exits without a result. `retry.pane_lost_backoff_seconds` (5) is the initial wait; it doubles with each retry, up to 60 s.
  These retries preserve the worktree and do not consume `workers.tier_order[].max_retries` or escalate to another lane.
- `workers.idle_timeout_seconds` (600) terminates workers with no activity signals; `workers.max_runtime_seconds`
  (3600) is the total limit. Both accept `0` to disable and can be overridden per lane in `tier_order`. Activity is
  detected by changes in the worktree's Git state or pane content; activity does not extend the limit. On timeout,
  existing changes and commits follow the normal scope, gate, and integration flow; a worker with no work is retried on the same lane and then escalates through the configured fallback chain.

**Gate and scope**
- `gate.commands`: custom checks (Go, Node/pnpm...); see `meister.config.example.yaml`. If no tests
  are detected, the gate fails unless `gate.allow_unverified: true`.
- `gate.docs_only` is an optional, disabled-by-default lightweight gate for changes where every changed file
  matches one of its relative `paths` patterns (default: root Markdown, Markdown under `docs/`, and `docs/img/`).
  Set `enabled: true` and define its own `commands`, for example `ruff check .` plus the documentation tests in
  `meister.config.example.yaml`. It replaces the full gate only before and after a merge; the final integration
  gate always runs the full checks. A failure that only the full gate would catch is therefore reported at the
  final gate and fails the entire run, rather than rejecting an individual task.
- `scope.tolerated_files`: tolerated files outside `target_files` (the list replaces the default). Each changed tolerated file emits a `scope_tolerated` event and a progress line. This repository tolerates `tests/**`; the full test suite must still pass.
- `gate.cache` (default `true`): the gate caches only successful passes, by code content and commands, and does not repeat
  an identical check; `gate.cache: false` disables it.
- `gate.repair_attempts` (default `1`; `0` disables): when the deterministic gate or strict scope check rejects a subtask, Meister does not discard the worktree. Instead, it reopens a worker on the same lane and worktree with a REPAIR section containing the rejection reason, failed tests list, and failure summary excerpt. The worker gets up to `repair_attempts` tries to fix collateral breakages before the subtask is archived and rejected.
- `environment.install_dependencies`: installs Node dependencies in worktrees only when there is something to install.

---

## Development and testing

The entire suite runs **in parallel** (`pytest-xdist`, `-n auto`) automatically when xdist is installed
(`pip install -e ".[dev]"`): about 50 s instead of 3.5 min. Selecting files or tests keeps execution
serial; use `-n0` or `MEISTER_TEST_PARALLEL=0` to force serial execution (debugging) or `MEISTER_TEST_PARALLEL=4` to
set the number of processes. Without xdist, nothing breaks: tests run serially. `pytest -v --durations=15`
lists the 15 slowest tests. By default, the suite interrupts each test after 180 seconds; adjust with
`MEISTER_TEST_TIMEOUT` or use `0` to disable the watchdog (on platforms with `SIGALRM`). An autouse
protection detects and removes branch and worktree refs accidentally created in the actual checkout; tests that
run the orchestrator should use a temporary Git repository. The CI test job has a total limit of 20 minutes.

Versioning: SemVer, `0.x` until the contract stabilizes. The version is in `meister/__init__.py` (and in the literals
in `package.json` and `herdr-plugin.toml`, checked by a test); see the [`CHANGELOG.md`](../CHANGELOG.md).

---

## Project structure

```
meisterrouter/
├── bin/
│   ├── meister                    # Python CLI executable
│   ├── install.sh                 # bootstrap and quick installation
│   └── cli.js                     # Node.js wrapper for npm / npx
├── meister/
│   ├── cli.py                     # command-line interface
│   ├── config.py, default_config.yaml  # configuration and model catalog
│   ├── plan.py, plan_adapters/    # canonical plan and importers
│   ├── plan_analysis.py           # plan parallelism analysis
│   ├── state.py                   # SQLite, state machine, circuit breakers
│   ├── jev.py, jev_context.py     # Jev client (OpenRouter) and context sent
│   ├── worker.py                  # worker execution (harnesses and Herdr tabs)
│   ├── worktree.py                # worktrees, integration, and fast-forward
│   ├── gate.py, env_setup.py      # deterministic gate and dependency setup
│   ├── logger.py, usage.py        # JSONL telemetry, tokens, credits, and cost
│   ├── report.py, progress.py     # report and terminal progress
│   ├── timeline*.py, log_tail.py  # TUI timeline (Gantt), read-only
│   ├── clean.py, hooks.py         # branch cleanup; Git/Claude hooks and guard
│   ├── faults.py                  # fault injection for crash tests
│   ├── herdr/                     # bridge.py (orchestrator), dag.py, workers.py, client.py, events.py, tui.py
│   ├── dashboard/                 # Flask server and web interface
│   └── templates/                 # CLAUDE.md, CODEX.md, hooks
├── tests/                         # test suite (pytest)
├── docs/                          # manual, diagrams, models and costs, this page
├── herdr-plugin.toml              # Herdr plugin manifest
├── package.json, pyproject.toml, setup.py   # npm and Python packages
├── CHANGELOG.md                   # version history
└── CLAUDE.md, CODEX.md, AGENTS.md # agent guidelines
```

---

## GitHub Copilot CLI adapter

> ℹ️ **STATUS: VERIFIED (GitHub Copilot CLI 1.0.88)**
> The Copilot CLI adapter (`copilot`) uses the official `-p`, `--allow-all`, and `--no-ask-user` flags for autonomous non-interactive execution.
> Its presence and position in the chain are defined by `meister/default_config.yaml` and can be changed in `meister.config.yaml`; model selection environment variables have been removed.
