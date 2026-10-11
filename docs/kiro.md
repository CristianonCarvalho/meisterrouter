# Kiro CLI as a harness

🌐 **English** · [Português (Brasil)](pt-BR/KIRO.md)

Meister accepts the [Kiro CLI](https://kiro.dev) as a worker harness. In `workers.tier_order` the harness is called `kiro-cli` (alias `kiro`). Each worker runs:

```bash
kiro-cli chat --no-interactive --trust-all-tools [--model M] [--effort E] "<prompt>"
```

Checked against kiro-cli 2.29.1: it runs without interaction, creates and edits files in the current directory (the task worktree), prints the final answer on stdout, prints tool logs (`[tool] Creating x`, `[tool] status: Completed`) on stderr, and exits with `0`. A model that does not exist exits with `1` and `Error: Internal error ... The model ... is not available` on stderr.

## Using only Kiro

You don't need Copilot, Claude, Codex or OpenRouter. Use `router.mode: first` (no Jev, no network) and list only Kiro lanes. The project's `tier_order` **replaces** the whole default list, so the other harnesses drop out:

```yaml
router:
  mode: first              # always start at the first lane; no OpenRouter key needed
workers:
  tier_order:              # the order is the fallback chain
    - {name: tier_1, harness: kiro-cli, model: auto, max_retries: 2}
    - {name: tier_1b, harness: kiro-cli, model: claude-haiku-4.5, max_retries: 2}
    - {name: tier_2, harness: kiro-cli, model: claude-sonnet-4.5, max_retries: 1}
gate:
  commands:                # optional: without it, the gate detects the project's tests
    - name: tests
      run: "{python} -m pytest -q"
      timeout_seconds: 600
      required: true
```

Leave out `gate.commands` and the gate uses automatic detection (for example pytest, `package.json` or `pom.xml`). Check the result with `meister config validate` and `meister models`. The other gate settings are in [advanced configuration](advanced.md#advanced-configuration).

## Models and cost

Valid models include `auto` (the default), `claude-sonnet-4.5`, `claude-haiku-4.5`, `deepseek-3.2`, `glm-5` and `qwen3-coder-next`. To list the models your account can use:

```bash
kiro-cli chat --list-models
```

Kiro charges credits, with a multiplier per model:

| Model | Credits |
|---|---|
| `auto` | 1.00x |
| `claude-sonnet-4.5` | 1.30x |
| `claude-haiku-4.5` | 0.40x |
| `qwen3-coder-next` | 0.05x |

## Effort

Meister accepts `effort` from `low` to `max` (`low`, `medium`, `high`, `xhigh`, `max`) on a Kiro lane and passes it as `--effort`. When the model does not support effort, Kiro only prints a warning on stderr and carries on without it.

## Limitations

- **No quota fallback.** Kiro's "quota exhausted" message has never been observed, so automatic fallback on quota does not trigger for Kiro. Errors with a non-zero exit code still follow the normal retry and fallback (`retry.worker_error_attempts`, then the next lane).
- **No usage in reports.** Kiro usage and credits do not appear in the usage reports (`meister report`, dashboard).

## Windows

The binary is `kiro-cli.exe`. Not verified on Windows.
