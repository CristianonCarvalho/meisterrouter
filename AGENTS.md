# MeisterRouter Unified Agent Directives (AGENTS.md)

This project uses **MeisterRouter** for deterministic, cost-optimized multi-model software engineering.

## Fundamental Principles
1. **Decision Separation & OpenRouter Boundary:**
   - **TypeSafe Jev** (`typesafe/jev-1.13` via OpenRouter) is EXCLUSIVELY used for deterministic state machine decisions (`meister classify`, `meister control`). OpenRouter is NEVER used for worker code generation.
   - All coding workers execute through their dedicated local agent harnesses:
     - **Codex Harness** (`codex` CLI): runs GPT-6 Luna or OpenAI models locally.
     - **Antigravity Harness** (`agy` CLI): runs Gemini 3.8 Flash models locally.
     - **Claude Harness** (`claude` CLI): runs Claude Haiku / Sonnet models locally.
     - **Copilot Harness** (`copilot` CLI): runs GitHub Copilot CLI models locally.
2. **Cost-Optimized Tiering & Mandatory Fallback:**
   - Default Implementer: **Codex / Luna** (`codex` CLI, $0.077/M tokens) or **Antigravity / Gemini 3.8 Flash** (`agy` CLI, $0.577/M tokens).
   - Deep Reasoning / Escalation: **Antigravity / Gemini 3.8 Flash** or **Claude 4.5 Haiku**.
   - Maximum Escalation: **Claude Sonnet 5** / **Claude Opus 5.5**.
   - ⚠️ **Zero Direct Implementation by Orchestrator:** If the recommended implementer (e.g. Luna) is inactive or unavailable, the orchestrator MUST NOT write code. It must dispatch immediately to **Gemini 3.8 Flash** or **Claude 4.5 Haiku**.
3. **Deterministic Evidence:** Evidence from tests, linters, and git diff always precedes completion declarations.
4. **Workspace Isolation & Sandboxing:**
   - Worker worktrees are placed strictly outside the repository root (by default in `~/.meister/worktrees/<repo_hash>/` or via `MEISTER_WORKTREES_DIR`), preventing workers from accessing repository root secrets (`.env`) via path traversal.
   - External worktrees provide filesystem segregation; full process and OS-level containment requires container/OS sandboxing. Workers use verified CLI flags confirmed via `--help`.

## Standard Execution Loop
```
[User Request]
       │
       ▼
1. meister classify --context "<task>"  ──► [Returns: classification + recommended implementer]
       │
       ▼
2. Dispatch code implementation to worker harness (ZERO direct implementation by orchestrator):
   • Primary: meister worker --model luna --task "<task>" [--files "<files>"]
   • If Luna fails/inactive, escalate immediately:
     meister worker --model gemini_flash --task "<task>" [--files "<files>"]
     or
     meister worker --model haiku --task "<task>" [--files "<files>"]
   • Or autonomous orchestration: meister orchestrate --task "<task>"
       │
       ▼
3. Run test runner, typechecker, and linter
       │
       ▼
4. meister control --diff-summary "<diff>" --test-result pass|fail
       │
       ├─► COMPLETE  ──► Git Commit & Done (Worker pane auto-closes)
       ├─► RETRY     ──► Switch implementer or retry code
       └─► ESCALATE  ──► Elevate reasoning level
```
