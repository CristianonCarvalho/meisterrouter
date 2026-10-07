# MeisterRouter Diagrams

Mermaid [diagrams](https://mermaid.js.org/) of how the system works (GitHub renders them directly on the page).
Each one is based on the code; the module, event, and state names are the actual ones.
The complete flow "from plan to `main`" is also shown in [`flow.md`](flow.md).

| # | Diagram | Answers |
|---|---|---|
| 1 | [Overview (context)](#1-overview-context) | Who is involved and what each one does |
| 2 | [Components](#2-components) | Which modules exist and how they connect |
| 3 | [Sequence of an `orchestrate`](#3-sequence-of-an-orchestrate) | What happens, in order, from a plan to `main` |
| 4 | [Lane selection](#4-lane-selection-routing) | How a subtask reaches a worker |
| 5 | [Worker failures, timeout, and quota](#5-worker-failures-timeout-and-quota) | What happens when the worker does not finish |
| 6 | [Integration and gates](#6-integration-and-gates) | Why nothing enters `main` without verification |
| 7 | [Run and subtask states](#7-run-and-subtask-states) | Valid transitions and resume |
| 8 | [Worktrees and branches](#8-worktrees-and-branches) | Where each worker's code lives |
| 9 | [Telemetry and visualization](#9-telemetry-and-visualization) | Where the dashboard, timeline, and report come from |
| 10 | [Claude Code guard](#10-claude-code-guard) | How the orchestrator is prevented from editing code |
| 11 | [Installation and Herdr integration](#11-installation-and-herdr-integration) | What the installer and `init` configure |

---

## 1. Overview (context)

You choose an **orchestrator LLM** (for example, Claude Code) to plan. Meister, which is deterministic code,
distributes tasks across your **subscriptions** (Copilot, Codex, Antigravity, Claude),
coordinates workers, and integrates the result. The only non-deterministic part of Meister is **Jev**, which only
chooses the initial lane and judges the result.

```mermaid
flowchart LR
  U(["You"])
  ORQ["Orchestrator LLM<br/>(Claude Code, Codex...)<br/>plans and reviews"]
  subgraph MR["MeisterRouter (Python, deterministic)"]
    CLI["CLI meister<br/>and daemon"]
    CORE["Orchestration:<br/>DAG, state, merge, gates"]
  end
  JEV["Jev (OpenRouter)<br/>classify and control<br/>only non-deterministic part"]
  HERDR["Herdr<br/>visible tabs, popups, shortcuts"]
  subgraph W["Workers (CLIs under your subscriptions)"]
    W1["copilot"]
    W2["codex"]
    W3["agy (Gemini)"]
    W4["claude"]
  end
  GIT[("Git repository<br/>worktrees and branches")]
  OBS["Dashboard, timeline<br/>and report"]

  U --> ORQ
  ORQ -- "plan.json" --> CLI
  CLI --> CORE
  CORE -- "which lane?" --> JEV
  JEV -- "lane and judgment" --> CORE
  CORE -- "opens tab and runs" --> HERDR
  HERDR --> W
  W -- "code in worktree" --> GIT
  CORE -- "gate, merge, fast-forward" --> GIT
  CORE -. "JSONL events" .-> OBS
  U -. "monitors" .-> OBS
  U -. "monitors" .-> HERDR
```

---

## 2. Components

Modules in `meister/` grouped by responsibility. Solid arrows are calls; dashed arrows
are data reads.

```mermaid
flowchart TB
  subgraph IF["Interface"]
    CLI["cli.py<br/>commands: orchestrate, worker, classify,<br/>control, plan, clean, report, timeline,<br/>dashboard, daemon, init, install-hooks"]
    HK["hooks.py + templates/<br/>Git and Claude hooks, guard"]
    PLG["herdr-plugin.toml<br/>plugin actions"]
  end

  subgraph CFGG["Configuration"]
    CFG["config.py<br/>load_config and validate_config"]
    DCFG["default_config.yaml<br/>+ project meister.config.yaml"]
  end

  subgraph PLAN["Plan"]
    PL["plan.py + plan_adapters/<br/>imports and validates canonical JSON"]
    PA["plan_analysis.py"]
    DAG["herdr/dag.py<br/>TaskDAG and independent batches"]
  end

  subgraph CORE["Orchestration"]
    BR["herdr/bridge.py<br/>HerdrEventBridge: run_orchestration_cycle,<br/>execute_plan, execute_subtask"]
    ST["state.py<br/>StateManager: SQLite WAL, FSM,<br/>circuit breakers"]
  end

  subgraph ROUTE["Routing"]
    JEV["jev.py<br/>classify_task and control_cycle"]
    JC["jev_context.py<br/>context sent to Jev"]
  end

  subgraph EXEC["Worker execution"]
    WS["herdr/workers.py<br/>WorkerSpawner, tiers, quota detection"]
    WK["worker.py<br/>HarnessWorker, Herdr tabs"]
    HC["herdr/client.py + events.py<br/>Herdr socket"]
  end

  subgraph INTG["Integration"]
    WT["worktree.py<br/>WorktreeManager and IntegrationPipeline"]
    GT["gate.py<br/>DeterministicGate and cache"]
    ENV["env_setup.py<br/>worktree dependencies"]
  end

  subgraph TEL["Telemetry"]
    LG["logger.py<br/>orchestration_log.jsonl"]
    US["usage.py<br/>tokens, credits, cost"]
    RP["report.py<br/>meister report"]
    DB["dashboard/<br/>server.py + metrics.py"]
    TL["timeline*.py + log_tail.py<br/>timeline (Gantt)"]
    PG["progress.py<br/>terminal progress"]
  end

  EXT1["OpenRouter"]
  EXT2["Herdr"]
  EXT3["CLIs: copilot, codex, agy, claude"]
  EXT4[("Git")]

  CLI --> BR
  CLI --> PL
  CLI --> CFG
  DCFG --> CFG
  PL --> DAG
  CLI --> PA
  BR --> DAG
  BR --> ST
  BR --> JEV
  BR --> JC
  BR --> WS
  WS --> WK
  WK --> HC
  BR --> WT
  BR --> GT
  WT --> GT
  WT --> ENV
  GT --> ENV
  JEV --> EXT1
  HC --> EXT2
  WK --> EXT3
  WT --> EXT4
  BR --> LG
  WK --> LG
  JEV --> LG
  GT --> LG
  US --> LG
  LG -.-> RP
  LG -.-> DB
  LG -.-> TL
  LG -.-> PG
  PLG --> CLI
  CLI --> HK
```

---

## 3. Sequence of an `orchestrate`

A plan with one subtask, from the command to `main`. With multiple independent subtasks, the block
"for each subtask" runs in parallel, and only the merge is serialized (merge lock).

```mermaid
sequenceDiagram
  autonumber
  actor O as Orchestrator LLM (you)
  participant C as CLI meister
  participant B as Bridge (orchestrator)
  participant S as State (SQLite)
  participant J as Jev (OpenRouter)
  participant H as Herdr
  participant W as Worker (subscription CLI)
  participant T as Worktrees and Git
  participant G as Deterministic gate

  O->>C: meister orchestrate --plan-file plano.json
  C->>C: loads and validates configuration (invalid: rc 2, nothing is created)
  C->>B: run_orchestration_cycle
  B->>B: load_plan, builds the DAG and independent batches
  B->>S: run RUNNING, orchestration_start
  B->>T: creates integration branch and worktree

  loop for each batch of independent tasks
    par for each subtask in the batch
      B->>J: classify (router.mode = jev)
      J-->>B: class and recommended lane
      Note over B: eligibility, cooldown, circuit breaker, and slot per lane
      B->>T: creates subtask worktree and branch
      B->>H: opens visible tab and runs the worker
      H->>W: executes the task in the worktree
      W-->>H: finishes
      H-->>B: pane result
      B->>G: scope and gate in the worker's worktree
      G-->>B: approved
      B->>T: worker commit
      Note over B,T: merge lock: one subtask at a time
      B->>T: merge --no-ff into the integration branch
      B->>G: integration gate (post-merge)
      G-->>B: approved (if it fails: rollback and subtask rejected)
      B->>S: subtask COMPLETED with integrated_sha
    end
  end

  B->>G: final gate on the integration branch
  B->>J: control (judges the result)
  J-->>B: COMPLETE
  B->>T: checks that each COMPLETED subtask is an ancestor of the integration branch
  B->>T: fast-forward of main (the only point that touches main)
  B->>S: run COMPLETED, orchestration_end
  B->>T: cleans up worktrees and branches
  B->>H: closes the tabs
  B-->>C: completed
  C-->>O: terminal summary
```

---

## 4. Lane selection (routing)

`router.mode` determines who chooses the initial lane. Eligibility (`eligible_classes`) and the circuit breaker can
change the choice; the result is recorded in `route_decision`.

```mermaid
flowchart TD
  A["Ready subtask"] --> B{"router.mode"}
  B -- "first" --> C["1st worker lane in workers.tier_order<br/>(no network)"]
  B -- "jev (default)" --> D{"Is Jev in cooldown?"}
  D -- "yes" --> C
  D -- "no" --> E["classify on Jev<br/>up to router.max_attempts attempts,<br/>timeout router.timeout_seconds"]
  E -- "failed or slow" --> F["sets cooldown<br/>router.unavailable_cooldown_seconds"]
  F --> C
  E -- "class + recommended lane" --> G{"Does the lane accept the class?<br/>eligible_classes"}
  G -- "yes" --> H["chosen lane"]
  G -- "no" --> I["switches to the nearest eligible lane<br/>earlier in the list, otherwise later, otherwise keeps it<br/>route_decision: ineligible_replaced"]
  I --> H
  C --> H
  H --> J{"Is the lane circuit breaker open?"}
  J -- "yes" --> K["next available lane<br/>tier_skipped_breaker"]
  J -- "no" --> L["waits for a lane slot<br/>max_parallel"]
  K --> L
  L --> M["worker in a Herdr tab"]
```

---

## 5. Worker failures, timeout, and quota

The worker pane may finish successfully, run out of quota, disappear, or time out. Meister never assumes
the implementation: it either integrates the work, retries, or moves to the next lane.

```mermaid
flowchart TD
  A["Worker running in tab"] --> B{"What happened?"}
  B -- "finished" --> OK["result received"]
  B -- "quota or rate limit (429)" --> Q["opens the lane circuit breaker<br/>quota_error"]
  Q --> NX["next available lane in tier_order"]
  NX --> A
  B -- "pane disappeared (detected in ~5 s)" --> PL{"pane_lost<br/>attempts available?"}
  PL -- "yes" --> RT["worker_retry on the same lane"]
  RT --> A
  PL -- "no" --> INF["infrastructure error<br/>does not switch lanes"]
  B -- "timed out" --> TO["stops the pane<br/>worker_timeout"]
  TO --> RES{"is there a result or<br/>worktree changes?"}
  RES -- "yes" --> SAL["work salvaged<br/>worker_timeout_salvaged"]
  RES -- "no" --> RTO{"attempts remaining?"}
  RTO -- "yes" --> RT
  RTO -- "no" --> ESC{"is there a next lane?"}
  ESC -- "yes" --> NX
  ESC -- "no" --> FAIL["subtask FAILED"]
  OK --> GATE["gate and integration"]
  SAL --> GATE
  GATE -- "rejected (gate, scope, merge)<br/>or worker with an error" --> REJ["subtask_rejected<br/>subtask FAILED, does not switch lanes"]
  REJ --> RUNF
  GATE -- "approved" --> DONE["subtask_completed"]
  INF --> RUNF["run FAILED<br/>running the same command resumes"]
  FAIL --> RUNF
```

---

## 6. Integration and gates

The **gate** is deterministic verification (by default `ruff` and `pytest`, or the commands in `gate.commands`).
It runs at three points. An approved gate is cached by code content and commands
(`gate.cache`), so identical verification is not repeated.

```mermaid
flowchart TD
  W["Worker finishes in the subtask worktree"] --> S{"Scope: did it only change<br/>target_files?"}
  S -- "no" --> R1["rejected: scope violation"]
  S -- "yes" --> G1["Gate 1: in the worker's worktree<br/>(in parallel across subtasks)"]
  G1 -- "failed" --> R2["rejected: gate failed"]
  G1 -- "ok" --> CM["worker commit"]
  CM --> LK["merge lock<br/>(one subtask at a time)"]
  LK --> MG["merge --no-ff into branch<br/>meister/integration/run"]
  MG -- "conflict" --> R3["rejected: merge failed"]
  MG -- "ok" --> G2["Gate 2: in the integration worktree<br/>(serially, under the lock)"]
  G2 -- "failed" --> RB["merge rollback<br/>rejected"]
  G2 -- "ok" --> CP["COMPLETED + integrated_sha"]
  CP --> MORE{"more subtasks?"}
  MORE -- "yes" --> W
  MORE -- "no" --> G3["Gate 3: final, on the integration branch"]
  G3 -- "failed" --> RF["run FAILED<br/>main intact"]
  G3 -- "ok" --> JC["Jev control: COMPLETE?"]
  JC -- "no" --> RF
  JC -- "yes" --> AN["are all COMPLETED subtasks<br/>ancestors of the integration branch?"]
  AN -- "no" --> RF
  AN -- "yes" --> FF["fast-forward of main"]

  CACHE[("gate cache<br/>Git tree + command hash,<br/>approvals only")]
  G1 -.-> CACHE
  G2 -.-> CACHE
  G3 -.-> CACHE
```

---

## 7. Run and subtask states

State machines in `meister/state.py` (invalid transitions raise an error). A `FAILED` or
`RUNNING` run returns to `RUNNING` when the same plan is run: subtasks already `COMPLETED` are skipped.

```mermaid
stateDiagram-v2
  direction LR
  state "Run" as RUN {
    [*] --> PENDING
    PENDING --> RUNNING
    PENDING --> CANCELLED
    RUNNING --> COMPLETED
    RUNNING --> FAILED
    RUNNING --> CANCELLED
    FAILED --> RUNNING: resume
    FAILED --> PENDING
    FAILED --> CANCELLED
    COMPLETED --> RUNNING
    CANCELLED --> RUNNING
    CANCELLED --> PENDING
  }
```

```mermaid
stateDiagram-v2
  direction LR
  state "Subtask" as SUB {
    [*] --> PENDING
    PENDING --> RUNNING
    PENDING --> FAILED
    RUNNING --> COMPLETED
    RUNNING --> FAILED
    RUNNING --> RETRYING
    RETRYING --> RUNNING
    RETRYING --> FAILED
    FAILED --> PENDING: resume
    FAILED --> RUNNING
    FAILED --> RETRYING
    COMPLETED --> RUNNING
    COMPLETED --> PENDING
  }
```

---

## 8. Worktrees and branches

Each subtask works in its own worktree; the merge happens on an integration branch, and `main` only
advances via fast-forward at the end, after all gates.

```mermaid
gitGraph
  commit id: "main (base)"
  branch meister/integration/run
  checkout main
  branch meister/worktree/tarefa-1
  checkout meister/worktree/tarefa-1
  commit id: "worker 1"
  checkout main
  branch meister/worktree/tarefa-2
  checkout meister/worktree/tarefa-2
  commit id: "worker 2"
  checkout meister/integration/run
  merge meister/worktree/tarefa-1 id: "merge --no-ff + gate"
  merge meister/worktree/tarefa-2 id: "merge --no-ff + gate 2"
  checkout main
  merge meister/integration/run id: "fast-forward"
```

Notes:
- Unintegrated work from a discarded subtask remains in `refs/meister/archive/<tarefa>-<data>` and
  is removed by Meister after 7 days. `meister clean` deletes old branches.
- Fast-forward is the only step that touches `main`. If any gate fails, `main` remains intact.

---

## 9. Telemetry and visualization

Everything is recorded as events in `orchestration_log.jsonl` (in the project's `.meister/logs/`, or in
`MEISTER_LOG_DIR`). The screens only read this file: they do not change anything.

```mermaid
flowchart LR
  subgraph PROD["Who writes"]
    P1["bridge and worker<br/>worker_spawn, worker_phase,<br/>subtask_*, orchestration_*"]
    P2["Jev<br/>classify, control, route_decision"]
    P3["gate and worktree<br/>gate phase: cached, saved_seconds"]
    P4["usage.py<br/>tokens, credits,<br/>reported or estimated cost"]
  end
  LOG[("orchestration_log.jsonl<br/>current_run.json")]
  subgraph CONS["Who reads"]
    D["meister dashboard<br/>web on localhost:5050<br/>and TUI (--tui)"]
    TLN["meister timeline<br/>Gantt: worker, gate, integration,<br/>queue, wait, Jev line"]
    RPT["meister report<br/>cost, time, attempts"]
    PRG["terminal progress<br/>from orchestrate"]
  end
  CFGY["config: tier_order, credit_usd,<br/>cost_per_m_tokens"]

  P1 --> LOG
  P2 --> LOG
  P3 --> LOG
  P4 --> LOG
  LOG -.-> D
  LOG -.-> TLN
  LOG -.-> RPT
  LOG -.-> PRG
  CFGY -.-> RPT
  CFGY -.-> D
  CFGY -.-> TLN
```

**Inside the timeline** (`meister timeline`), three separate layers:

```mermaid
flowchart LR
  LT["log_tail.py<br/>incremental, read-only"] --> M["timeline.py<br/>pure model:<br/>events to Timeline"]
  M --> V["timeline_view.py<br/>pure rendering:<br/>Timeline to ANSI lines"]
  V --> A["timeline_app.py<br/>keyboard, zoom, pause,<br/>live mode and replay"]
  CLI["timeline_cli.py<br/>--once, --all, --run-id"] --> M
  CLI --> V
```

---

## 10. Claude Code guard

`PreToolUse` hook installed by `meister init --hooks` (or `install-hooks --claude`). Prevents the orchestrator LLM
from writing code directly: workers implement changes in these projects.

```mermaid
flowchart TD
  A["Claude is about to use Edit, Write,<br/>MultiEdit, or NotebookEdit"] --> B{"Bypass variable?<br/>MEISTER_IN_PANE=1<br/>MEISTER_ALLOW_ORCHESTRATOR_EDIT=1<br/>or .meister/allow_orchestrator"}
  B -- "yes" --> OK["allows"]
  B -- "no" --> C{"Can it read the call?"}
  C -- "no" --> BL["blocks (fail closed)"]
  C -- "yes" --> D{"Is the path documentation?<br/>docs/**, *.md, *.mdx, *.txt<br/>no .. component"}
  D -- "yes" --> OK
  D -- "no" --> E{"Mode in .meister/guard_mode"}
  E -- "block (default)" --> BL2["refuses and advises:<br/>use meister orchestrate"]
  E -- "ask" --> AS["asks for your confirmation"]
  E -- "off" --> OK
```

---

## 11. Installation and Herdr integration

Herdr is a prerequisite. The installer calls `meister setup`, which does the rest and is safe to run again.

```mermaid
flowchart TD
  H["Herdr installed<br/>(prerequisite)"] --> I1
  subgraph INS["Once per machine"]
    I1["bin/install.sh<br/>aborts if it cannot find herdr"] --> I2["clones into ~/.local/share/meisterrouter<br/>creates .venv and installs the package"]
    I2 --> I3["symlink ~/.local/bin/meister"]
    I3 --> I4["meister setup"]
    I4 --> S1["herdr plugin link<br/>only if the plugin is not already enabled"]
    I4 --> S2["shortcuts: managed block in config.toml<br/>backup, herdr config check, reload;<br/>never overwrites your shortcuts"]
    I4 --> S3["diagnostics: Python, daemon, worker CLIs,<br/>Jev key, and configuration"]
    I7["OPENROUTER_API_KEY<br/>(only Jev uses it)"]
  end
  subgraph PRJ["Once per project"]
    P1["meister setup --project"] --> P2["CLAUDE.md, CODEX.md, AGENTS.md<br/>.meister/logs"]
    P1 --> P3["Git pre-commit hook<br/>Claude hooks and guard in .claude/"]
    P4["meister config init<br/>(optional): lanes and order"]
  end
  subgraph USO["Day to day (Herdr)"]
    U1["prefix+m<br/>orchestrate"]
    U2["prefix+shift+m<br/>dashboard in popup"]
    U3["prefix+t<br/>timeline in popup"]
    U4["worker tabs<br/>visible in real time"]
  end
  S1 --> PRJ
  S2 --> USO
  PRJ --> USO
```
