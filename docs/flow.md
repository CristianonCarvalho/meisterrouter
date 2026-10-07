# MeisterRouter Flow

End-to-end view of the process, from the plan to `main`. Generated on 2026-10-01 from the code merged through PR #17.

```mermaid
flowchart TD
  subgraph PLAN["1. Planning"]
    A["Architect: LLM that plans<br/>(superpowers: writing-plans)"] --> B["plano.md"]
    B --> C["meister plan import<br/>adapter and schema validation"]
    C --> D[("canonical plano.json<br/>id, target_files, depends_on")]
  end

  subgraph CFG["Configuration"]
    CF1["default_config.yaml<br/>(packaged)"] --> CF3["load_config and validate_config"]
    CF2["meister.config.yaml<br/>(project, optional)"] --> CF3
  end

  D --> O["meister orchestrate --plan-file"]
  CF3 --> O
  O --> V{"Valid config?"}
  V -- "no" --> X["rc 2: nothing is created"]
  V -- "yes" --> S[("SQLite: run, subtasks,<br/>circuit breakers")]
  S --> G["DAG: batches of independent tasks<br/>same file or no scope = sequential"]

  subgraph SUB["2. Each subtask in the batch, in parallel"]
    G --> R{"router.mode"}
    R -- "jev (default)" --> J["Jev on OpenRouter chooses the initial lane"]
    J -- "failure or slow" --> CD["cooldown: uses the first lane"]
    R -- "first" --> T1["1st lane in the list"]
    J --> SL
    CD --> SL
    T1 --> SL["Slot per lane (max_parallel)<br/>and circuit breaker, always forward"]
    SL --> W["Own worktree"]
    W --> HT["Worker in a visible Herdr tab<br/>copilot, codex, agy or claude"]
    HT --> WAIT{"Result"}
    WAIT -- "429 quota" --> FB["Opens the circuit breaker<br/>and tries the next lane"]
    FB --> SL
    WAIT -- "pane disappeared (5 s)" --> ERR["Infrastructure error<br/>without switching lanes"]
    WAIT -- "completed" --> GATE
  end

  subgraph INT["3. Deterministic integration"]
    GATE["Deterministic gate in the worktree:<br/>scope and tests"] --> CM["Worker commit"]
    CM --> MG["merge --no-ff on the<br/>integration branch"]
    MG --> IG{"Integration gate"}
    IG -- "failed" --> RB["Rollback: subtask rejected"]
    IG -- "ok" --> DONE["COMPLETED + integrated_sha"]
  end

  ERR --> FAIL["Run FAILED<br/>running the same command resumes"]
  RB --> FAIL
  DONE --> NEXT{"Are there more batches?"}
  NEXT -- "yes" --> G
  NEXT -- "no" --> JC["Jev control<br/>judges the result"]
  JC --> FF["Fast-forward main"]
  FF --> CL["Cleanup: worktrees,<br/>branches and tabs"]

  subgraph OBS["Observability (Herdr plugin)"]
    LOG[("JSONL events")]
    DASH["Dashboard and TUI"]
    DMN["Daemon: Herdr events"]
  end
  O -.-> LOG
  HT -.-> LOG
  LOG -.-> DASH
  DMN -.-> HT
```

## How to read

- **1. Planning:** The plan becomes validated JSON, and the configuration is checked before anything else. Invalid configuration exits with rc 2 without creating anything.
- **2. Each subtask:** Jev (or the first lane) chooses the initial lane. There is a slot per lane and a worker in a visible Herdr tab. An exhausted quota opens the circuit breaker and moves to the next lane; a pane that disappears is detected within 5 s.
- **3. Integration:** Nothing enters `main` without passing the gates. A failure at any stage leaves `main` untouched, and running the same command resumes where it stopped.

## Roles

| Role | Who it is | Does |
|---|---|---|
| Architect | LLM that plans (currently the Claude Code session) | Generates the plan |
| Orchestrator | Python code (`meister orchestrate`), no LLM | Queue, worktrees, merge, retries, quota |
| Jev | Model on OpenRouter | Chooses the initial lane (`classify`) and judges the result (`control`) |
| Workers | `copilot`, `codex`, `agy`, `claude` | Implement the subtasks |
