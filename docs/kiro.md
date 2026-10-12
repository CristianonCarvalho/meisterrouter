# Kiro CLI as a harness

🌐 **English** · [Português (Brasil)](pt-BR/KIRO.md)

Meister accepts the [Kiro CLI](https://kiro.dev) as a worker harness. In `workers.tier_order` the harness is called `kiro-cli` (alias `kiro`). Each worker runs:

```bash
kiro-cli chat --no-interactive --trust-all-tools [--model M] [--effort E] "<prompt>"
```

Checked against kiro-cli 2.29.1: it runs without interaction, creates and edits files in the current directory (the task worktree), prints the final answer on stdout, prints tool logs (`[tool] Creating x`, `[tool] status: Completed`) on stderr, and exits with `0`. A model that does not exist exits with `1` and `Error: Internal error ... The model ... is not available` on stderr.

## Using only Kiro

You don't need Copilot, Claude, Codex or OpenRouter. At the project root, generate the ready-made configuration:

```bash
meister config init --kiro        # writes ./meister.config.yaml (--path for another place, --force to overwrite)
meister config validate
meister models
```

The file uses `router.mode: first` (no Jev, no network, no OpenRouter key) and three Kiro lanes that form the fallback chain:

| Lane | Model | Attempts |
|---|---|---|
| `tier_1` | `auto` | 2 |
| `tier_1b` | `claude-haiku-4.5` | 2 |
| `tier_2` | `claude-sonnet-4.5` | 1 |

The project's `tier_order` **replaces** the whole default list, so the other harnesses drop out. To change the models, edit the file; `kiro-cli chat --list-models` shows the ones your account can use.

Without `gate.commands`, the gate uses automatic detection: `pom.xml` (Maven), `package.json`, `Cargo.toml`, or pytest when there is a `pytest.ini`, `conftest.py`, a `setup.cfg` with `[tool:pytest]` or a `pyproject.toml`. If the project has none of those, the run stops with "No tests or linters detected" as a gate infrastructure error: the worker is not blamed, its work is archived and no repair is spent (`meister.config.yaml` is protected and only the owner edits it). Declare the `gate.commands` block yourself (the generated file has a commented example) and rerun the same command. The other gate settings are in [advanced configuration](advanced.md#advanced-configuration).

Checked with `kiro-cli` 2.29.1: `orchestrate` with this file completed a task in 14 s and the gate passed.

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
