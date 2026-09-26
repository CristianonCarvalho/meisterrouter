# OpenAI Codex → MeisterRouter — Autonomous Multi-Model Orchestration

This codebase is governed by the **MeisterRouter** framework. You are operating as the **Primary Architect and Orchestrator**.

## 01. Core Rule of Operation
Never blindly implement code or make unverified assumptions without passing through the deterministic control loop.
All task sizing, subagent routing, and exit evaluations are governed by **TypeSafe Jev Decisions API** (`typesafe/jev-1.13`).

## 02. Multi-Model Matrix

| Model | Assigned Role | Capabilities | Cost / 1M tokens |
|---|---|---|---|
| **GPT-6 Luna (medium)** | **Primary Worker (Fast & Low Cost)** | Intelligence: 29 \| Automation: 40% | **$0.077** (90% savings) |
| **Gemini 3.8 Flash** | **Deep Reasoning & Code Fixer** | Intelligence: 40 \| Terminal-Bench: 20% | $0.5775 (Heavy Lift) |
| **OpenAI Codex / GPT-4o** | **Architect & Plan Supervisor** | Intelligence: 30 \| SciCode: 50% | $2.50 / $10.00 |
| **TypeSafe Jev-1.13** | **State Machine & Exit Gate** | Typed Probabilistic Decisions | $0.50 |

## 03. Agent Execution Lifecycle

### Phase 1: Classification Gate
Run before beginning implementation:
```bash
./bin/meister classify --context "<detailed task description>"
```

### Phase 2: Implementation Delegation
- For `SMALL` or `MEDIUM` tasks: Use low-cost execution (**GPT-6 Luna**, **Gemini 3.8 Flash**, or **Claude 4.5 Haiku**).
- For `HIGH` tasks or complex system debugging: Use **Gemini 3.8 Flash**.
- Independent files/subtasks must be processed in parallel across worker sessions.

> ⚠️ **CRITICAL DIRECTIVE: ORCHESTRATOR MUST NEVER IMPLEMENT DIRECTLY**
> If the recommended implementer (e.g. **Luna**) is inactive, unavailable, or errors:
> 1. **DO NOT write the implementation code yourself as the architect.**
> 2. **Immediately fallback to the next tier:** delegate to **Gemini 3.8 Flash** (`gemini_flash`) or **Claude 4.5 Haiku** (`haiku`).
> 3. Only if all workers are provably unavailable may the orchestrator implement directly, explicitly stating why.

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
