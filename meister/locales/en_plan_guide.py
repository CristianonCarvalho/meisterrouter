"""English catalog for the plan format guide (`meister plan guide`)."""

MESSAGES: dict[str, str] = {
    "plan_guide.title": "# MeisterRouter plan format",
    "plan_guide.intro": "A plan is a Markdown file with one `### Task N: name` block per task. Read this guide before writing one.",
    "plan_guide.flow.heading": "## Workflow",
    "plan_guide.flow.body": "Read the guide, create the skeleton, fill in the tasks, convert to canonical JSON, check it and run it:",
    "plan_guide.split.heading": "## Many small tasks",
    "plan_guide.split.body": (
        "- Split as much as you can: many small tasks, not a few big ones.\n"
        "- 3 to 5 files per task (up to about 8 only for mechanical changes such as renames).\n"
        "- One testable deliverable per task; the whole test suite stays green at the end of each task.\n"
        "- Short description, about 1.5 thousand tokens: only what that worker needs."
    ),
    "plan_guide.files.heading": "## **Files:** is the scope",
    "plan_guide.files.body": (
        "- List every file the task creates, modifies or tests, one bullet each: `- Create:`, `- Modify:` or `- Test:` followed by the path in backticks.\n"
        "- The gate rejects any change outside this list, so do not forget the test files.\n"
        "- Globs are accepted, for example `docs/*.md`."
    ),
    "plan_guide.depends.heading": "## **Depends on:** only for real dependencies",
    "plan_guide.depends.body": (
        "- Write `**Depends on:** Task 2, Task 5` only when the task needs the result of another one.\n"
        "- With `scope.allow_dependency_files` turned on, a task may also edit the files of its dependencies."
    ),
    "plan_guide.parallel.heading": "## Parallelism",
    "plan_guide.parallel.body": (
        "- Tasks that share no files and have no **Depends on:** run in parallel.\n"
        "- Check the expected parallelism with `meister plan analyze plan.json`."
    ),
    "plan_guide.dont.heading": "## What NOT to do",
    "plan_guide.dont.body": (
        "- Do not quote error messages from model providers in a task description: the worker output is scanned for them and the task is discarded.\n"
        "- Do not ask a task to edit `meister.config.yaml`: the configuration is not part of the plan.\n"
        "- Do not write giant tasks with many files or several deliverables: split them."
    ),
    "plan_guide.examples.heading": "## Examples",
    "plan_guide.examples.markdown": "Markdown (convert with `meister plan import`):",
    "plan_guide.examples.json": "Canonical JSON (check with `meister plan validate`):",
    "plan_guide.example.title": "# Greeting feature",
    "plan_guide.example.constraint": "- Keep the public API backwards compatible.",
    "plan_guide.example.task1.name": "Greeting function",
    "plan_guide.example.task1.body": "Add greet(name) returning a greeting, with tests.",
    "plan_guide.example.task2.name": "Greeting command",
    "plan_guide.example.task2.body": "Add the greet command that calls greet(name), with tests.",
}
