# Data sent to Jev

Jev (`typesafe/jev-1.13` via OpenRouter) makes two kinds of decisions: **classify** (which lane
to start with) and **control** (complete, retry, or switch lane). Only these two calls leave the
machine. Workers never send data to OpenRouter; they run through their local harnesses.

This page lists exactly what each call sends. The contract is enforced by
`tests/test_jev_payload_contract.py`, which fails if a field is added or changed, so this page
must be updated with the change.

Back to [advanced commands](advanced.md#the-openrouter-key).

## Request shape

Both calls send a `POST` with a JSON body of the form `{model, temperature, state, questions}`.

- `model`: `--model` or `master.model` (default `typesafe/jev-1.13`).
- `temperature`: always `0.0`.
- `state`: the facts Jev decides on.
- `questions`: the choices Jev must answer, each with its allowed criteria.

The API key is sent only in the `Authorization: Bearer <key>` header. It never appears in the
body and is never printed or logged. The headers also include `HTTP-Referer` and `X-Title`.

## `classify`

Sent by `meister classify` and by `orchestrate` for each subtask.

| Field | Origin | Example |
|---|---|---|
| `state.task_description` | The context string. In `orchestrate`, it is built by `build_jev_context` from the plan task. The CLI sends `--context` as written. | `Tarefa: Adicionar filtro\nArquivos (1): src/Filter.tsx\n...` |
| `questions.complexity` | Fixed: criteria `small`, `medium`, `high`, `escalate`. | `{"criteria": {...}}` |
| `questions.recommended_implementer` | Built from `workers.tier_order`. Each lane gets an opaque key (`lane_a`, `lane_b`, ...) and a description: `<model or harness> via <harness> ($cost/M): <best_for>`. | `"lane_a": "gpt-6-luna via codex ($0.20/M): ..."` |

Lane names (`tier_1`, ...) are never sent. They are replaced by opaque keys (`_opaque_lane_keys` in
`meister/jev.py`) because names like `tier_N` made Jev escalate to higher lanes. Jev's answer is
translated back to the real lane name. An unknown key falls back to the first lane.

### What `task_description` contains

`task_description` is the **text of the plan task**: its title, its `Files` list, its `Depends on`
list, and the body, truncated to `router.context_max_chars` (default 4000). Global project
constraints are omitted and only their size is reported. Anything written in the task body is
sent, so do not put secrets in plan tasks.

## `control`

Sent by `orchestrate` after the gate, before the `main` fast-forward, and by `meister control`.

| Field | Origin | Example |
|---|---|---|
| `state.diff_summary` | `--diff-summary`, or `git diff --stat` in the worktree, or `git diff --stat base..HEAD` for the integration. | `src/Filter.tsx \| 12 +++-` |
| `state.test_result` | `pass`, `fail`, or `unknown`. | `pass` |
| `state.attempts_so_far` | Attempt number (default 1). | `2` |
| `state.security_sensitive` | Flag (`false` by default). | `false` |
| `questions.next_action` | Fixed: `COMPLETE`, `RETRY`, `switch_implementer`. | |
| `questions.should_escalate` | Fixed: yes/no. | |
| `questions.switch_implementer` | Fixed: yes/no. | |

### What `diff_summary` contains

`diff_summary` **includes file paths**: `git diff --stat` lists every changed file. It does not
include file contents, but paths can reveal project structure.

## Avoiding the send

- **`router: {mode: first}`** in `meister.config.yaml` skips Jev for routing in `orchestrate`.
  The first lane is used and the `tier_order` fallback still works. This is the way to keep
  `classify` off the network.
- **No `OPENROUTER_API_KEY`**: the key lookup fails before any request is made. The call falls back
  to deterministic rules with no network access.
- **`control` is still sent at the end of `orchestrate`** even with `router.mode: first`, because the
  mode only changes routing. Without a key, `control` falls back to local rules and sends nothing.

## Local logs

The `classify` event written to the local JSONL log contains the first 300 characters of the context,
its length, and its SHA-256. The log stays under `MEISTER_LOG_DIR` and is not sent anywhere by
Meister.
