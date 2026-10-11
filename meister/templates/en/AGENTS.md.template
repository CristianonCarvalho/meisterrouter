# Agent Directives

## Project context

- **Stack:** <!-- fill in the languages and tools used by this project -->
- **Test command:** <!-- fill in the command used to run tests -->
- **Conventions:** <!-- fill in code patterns and project practices -->

## Task execution

- Follow the scope and target files specified in the assigned task.
- Preserve pre-existing changes and avoid changes outside the scope.
- Run relevant tests and clearly report results and limitations. Do NOT run the entire test suite: MeisterRouter's gate runs it afterward.

## Writing a plan for MeisterRouter

If you are asked to write a plan (not to implement a subtask), use the native flow:

```bash
meister plan guide
meister plan new -o plan.md
meister plan import plan.md -o plan.json
meister plan validate plan.json
```

Read the rules with `plan guide`, fill in the tasks in the skeleton, then convert and validate
it; the architect runs the validated plan. If you use the `superpowers:writing-plans` skill,
the format is the same (accepted by `plan import`).

## If you are a MeisterRouter worker

Consider yourself a worker if `MEISTER_WORKER=1` (or, in older installations, `MEISTER_IN_PANE=1`) is set in the environment or if you
are in a worktree created for a subtask. Implement the assigned task directly,
only in the target files. Run relevant tests and report the evidence. Do NOT run the
entire test suite: MeisterRouter's gate runs it afterward.
Do not run MeisterRouter classification, worker execution, orchestration, or control
commands; do not delegate the task to another agent.
