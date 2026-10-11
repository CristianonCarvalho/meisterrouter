# Codex → MeisterRouter — Architect Guide

This project uses MeisterRouter to organize engineering work with deterministic
decisions, verifiable execution, and flexible configuration.

## Roles

- **Architect:** understands the request, defines the plan, and oversees quality; does not
  implement code directly.
- **Orchestrator:** executes the plan, coordinates subtasks, and integrates changes.
- **Jev:** provides deterministic classification and control decisions.
- **Workers:** implement assigned subtasks and validate their changes. Workers identify
  themselves with `MEISTER_WORKER=1` (or `MEISTER_IN_PANE=1` in older installations).

Implementation tasks are executed by the orchestrator; the architect plans,
tracks evidence, and decides next steps.

## Workflow

1. For work with multiple steps, read the planning rules and create the plan skeleton:

   ```bash
   meister plan guide
   meister plan new -o plan.md
   ```

   Fill in the tasks in `plan.md`. If you use the `superpowers:writing-plans` skill, the format
   is the same (accepted by `plan import`).
2. Convert it to the canonical format and review the tasks and dependencies:

   ```bash
   meister plan import plan.md -o plan.json
   meister plan validate plan.json
   ```

3. Execute the validated plan:

   ```bash
   meister orchestrate --plan-file plan.json
   ```

4. Use `meister classify` and `meister control` as supporting tools when useful for
   classifying a task or evaluating evidence and next steps.
5. Run the appropriate tests, type checks, and linters for the project; review the diff
   before finishing.

## Configuration

See `meister config show` or `meister models` for the lanes configured in the
current environment. To adjust the catalog, edit `meister.config.yaml` using
`meister.config.example.yaml` in the MeisterRouter repository as a reference. Do not assume
fixed lane names or default configurations.
