# MeisterRouter Unified Agent Directives (AGENTS.md)

This project uses **MeisterRouter** for deterministic, cost-optimized multi-model software engineering.

## Fundamental Principles
1. **Decision Separation:** Coding models write code; **TypeSafe Jev** (`typesafe/jev-1.13`) makes routing, retry, and completion decisions.
2. **Cost-Optimized Tiering:**
   - Default Implementer: **GPT-6 Luna** ($0.077/M tokens) or **Claude 4.5 Haiku**.
   - Deep Reasoning / Escalation: **Gemini 3.8 Flash** ($0.577/M tokens) or **Claude Sonnet 5**.
   - Maximum Escalation: **Claude Opus 5.5**.
3. **Deterministic Evidence:** Evidence from tests, linters, and git diff always precedes completion declarations.

## Standard Execution Loop
```
[User Request]
       │
       ▼
1. meister classify --context "<task>"  ──► [Returns: classification + recommended implementer]
       │
       ▼
2. Implement code using the recommended implementer tier
       │
       ▼
3. Run test runner, typechecker, and linter
       │
       ▼
4. meister control --diff-summary "<diff>" --test-result pass|fail
       │
       ├─► COMPLETE  ──► Git Commit & Done
       ├─► RETRY     ──► Switch implementer or retry code
       └─► ESCALATE  ──► Elevate reasoning level
```
