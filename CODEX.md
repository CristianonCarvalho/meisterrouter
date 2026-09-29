# OpenAI Codex → MeisterRouter — Autonomous Multi-Model Orchestration

This codebase is governed by the **MeisterRouter** framework. You are operating as the **Primary Architect and Orchestrator**.

## 01. Core Rule of Operation
Never blindly implement code or make unverified assumptions without passing through the deterministic control loop.
All task sizing, subagent routing, and exit evaluations are governed by **TypeSafe Jev Decisions API** (`typesafe/jev-1.13`).

| Model | Assigned Role | Local Harness / Executable | Cost / 1M tokens |
|---|---|---|---|
| **GPT-6 Luna (medium)** | **Primary Worker (Fast & Low Cost)** | Codex CLI (`codex`) | **$0.077** (90% savings) |
| **GitHub Copilot CLI** | **Code Implementer (GitHub)** | Copilot CLI (`copilot`) | $0.20 |
| **Gemini 3.8 Flash** | **Deep Reasoning & Code Fixer** | Antigravity CLI (`agy`) | $0.5775 (Heavy Lift) |
| **Claude 4.5 Haiku** | **Secondary Implementer** | Claude CLI (`claude`) | $0.77 |
| **OpenAI Codex / GPT-4o** | **Architect & Plan Supervisor** | Codex CLI (`codex`) | $2.50 / $10.00 |
| **TypeSafe Jev-1.13** | **State Machine & Exit Gate** | OpenRouter (Decisions Only) | $0.50 |

> 🔒 **OPENROUTER ISOLATION:** OpenRouter is strictly and exclusively used by TypeSafe Jev for deterministic state machine decisions (`classify` and `control`). Workers NEVER call OpenRouter; they run via local harnesses (`codex`, `agy`, `claude`, `copilot`).

## 03. Agent Execution Lifecycle

### Phase 1: Classification Gate
Run before beginning implementation:
```bash
./bin/meister classify --context "<detailed task description>"
```

### Phase 2: Implementation Delegation
Invoke the recommended worker using the MeisterRouter CLI:
```bash
# 1. Primary execution via recommended worker:
meister worker --model luna --task "<task description>" [--files "<comma_separated_files>"]

# 2. IF the recommended model fails or is inactive, ESCALATE IMMEDIATELY:
meister worker --model gemini_flash --task "<task description>" [--files "<files>"]
# or
meister worker --model haiku --task "<task description>" [--files "<files>"]

# 3. Or delegate full cycle to autonomous orchestrator:
meister orchestrate --task "<task description>"
```

> ⚠️ **CRITICAL DIRECTIVE: ZERO DIRECT IMPLEMENTATION BY ORCHESTRATOR**
> If the recommended implementer (e.g. **Luna**) is inactive, unavailable, or errors:
> 1. **DO NOT write the implementation code yourself as the architect.**
> 2. **Immediately fallback to the next tier:** delegate to **Gemini 3.8 Flash** (`gemini_flash`) or **Claude 4.5 Haiku** (`haiku`).
> 3. If Gemini fails, delegate to Haiku; if Haiku fails, escalate to Sonnet/Opus.
> 4. Only if all workers in the fallback chain are provably unavailable may the orchestrator implement directly, explicitly stating why.

### Phase 3: Deterministic Evidence Collection
```bash
pytest tests/
git diff
```

### Phase 4: Control & Exit Gate
```bash
./bin/meister control --diff-summary "<diff summary>" --test-result pass|fail
```
Proceed only when `action == "COMPLETE"`.

## 04. Observability & Dashboard
```bash
./bin/meister dashboard --port 5050
```
