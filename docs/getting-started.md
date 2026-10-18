# Getting started: install, first run and daily use

The [README](../README.md#-get-started) has the short version: install, `meister setup --project`, then ask your orchestrator LLM for what you want. This page has the details.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/CristianonCarvalho/meisterrouter/main/bin/install.sh | bash
```

The installer installs `meister`, checks your environment and lists what is still missing (worker CLIs, Jev key). To pin a version (tag) instead of `main`: `... | bash -s -- --version v0.12.0` (or `latest`). You can run `meister setup` again at any time: it does not redo what is already done (`--dry-run` shows what it would do).

Other ways to install (clone, npm, pip) are in [Alternative install and advanced commands](advanced.md). On Windows, use `pipx` or `pip` (the installer above is bash): see [Windows](advanced.md#windows).

**The Jev key** (optional, once; only Jev uses OpenRouter, the workers run on your own subscriptions):

```bash
echo 'OPENROUTER_API_KEY=sk-or-v1-...' >> ~/.meister/.env
```

**Each project**, once, from inside it: `meister setup --project` (creates `CLAUDE.md`, `CODEX.md`, `AGENTS.md`, the hooks and the guard).

## Update

How you update depends on how you installed. Check the result with `meister --version` (the commit shown is the code in use).

| Installed with | Update with |
|---|---|
| The `curl ... install.sh` installer | Run the same command again. It updates the clone in `~/.local/share/meisterrouter` (add `--version latest` or a tag to choose the version). |
| A clone with `pip install -e .` or `./bin/install.sh` | `git pull` inside the clone. The install is editable, so there is nothing to reinstall (run `pip install -e .` again only if `pyproject.toml` changed its dependencies). |
| `pipx` (Windows) | `pipx install --force git+https://github.com/CristianonCarvalho/meisterrouter.git` |
| `pip` | `pip install --upgrade git+https://github.com/CristianonCarvalho/meisterrouter.git` |
| `npm` | `npm install -g git+https://github.com/CristianonCarvalho/meisterrouter.git` |

After updating:

- **In each project**, run `meister setup --project` again. It does not redo what is already done, and it refreshes the managed pre-commit hook. A hook you edited by hand is kept; `meister init --hooks --force` replaces it.
- **A run in progress** keeps the code it loaded when it started. The new version applies from the next run.
- The [CHANGELOG](../CHANGELOG.md) lists what changed in each version.

## How to use

After `meister setup --project`, the project has the rules and hooks that **teach your orchestrator LLM to use Meister**. You do not type `meister plan` or `meister orchestrate`:

1. **Open your orchestrator LLM** (Claude Code or Codex) in the project.
2. **Ask in plain language**, for example: *"I want a date filter on the orders screen"*. It talks to you, designs the solution and writes the plan: it reads the rules with `meister plan guide` and starts from `meister plan new` (or, if you use the Superpowers skill, from the skill's plan).
3. **It does the rest**: imports and validates the plan, then runs it with Meister. `CLAUDE.md` and the prompt hook (`UserPromptSubmit`) tell it that the execution method is always Meister, and the **guard** (`PreToolUse`) refuses any direct code edit, so it delegates.
4. **You follow along** with the timeline and the dashboard:

   ```bash
   meister timeline      # live Gantt of tasks and phases
   meister dashboard     # metrics, cost and tasks at http://localhost:5050
   meister logs -f       # follow the run log
   meister wait --run-id <id>   # block until the run ends (exit code tells how it went)
   ```

5. **It reviews the evidence** (tests, diff) and wraps up. Commit, push and merge still depend on your approval.

If Meister is unavailable, the LLM asks before implementing by other means. If a run fails, ask it to run again: tasks already completed are skipped (what to do for each failure is in the [execution manual](execution-manual.md)).

## Running the commands yourself

Want to review or write a plan by hand, or run the commands yourself? The native flow is:

```bash
meister plan guide                         # the format rules
meister plan new -o plano.md               # an empty plan with N tasks (--tasks N)
meister plan import plano.md -o plano.json
meister plan validate plano.json
meister orchestrate --plan-file plano.json
```

See the [plan format](plan-format.md) and the [advanced commands](advanced.md).
