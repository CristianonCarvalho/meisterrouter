# Java 8, Maven and GitLab

🌐 **English** · [Português (Brasil)](pt-BR/JAVA_MAVEN.md)

How to use MeisterRouter on a Java 8 project built with Maven, on Windows, hosted on GitLab.

## What Meister detects

When the project root has a `pom.xml` and `gate.commands` is empty, the gate runs Maven by itself:

```
mvn -B -q test
```

The Maven executable is resolved in this order:

1. The project's Maven Wrapper: `mvnw.cmd` on Windows, `mvnw` elsewhere.
2. `mvn` on the `PATH` (on Windows it is found through `PATHEXT`, because it is `mvn.cmd`).
3. `%MAVEN_HOME%\bin\mvn.cmd`, then `%M2_HOME%\bin\mvn.cmd` (`bin/mvn` outside Windows).

If none is found, the gate fails with a message saying so. The variables used here come from the environment plus `gate.env` (below).

## Eclipse's embedded Maven does not work

The Maven embedded in Eclipse (m2e) is a set of libraries loaded by the IDE; it does not provide an `mvn` command the gate can call. To have the gate and the IDE use the same Maven:

1. Install an external Maven (unzip the Apache Maven archive, for example to `C:\tools\apache-maven-3.9.9`).
2. In Eclipse, open **Window > Preferences > Maven > Installations**, click **Add...**, select that folder and tick it as the default.
3. Point `MAVEN_HOME` to it (system variable or `gate.env`).

Alternative: the Maven Wrapper. Run `mvn -N wrapper:wrapper` once in the project and commit `mvnw`, `mvnw.cmd` and `.mvn/`. The wrapper comes first in the resolution order, so every worktree uses the same Maven version.

## Example `meister.config.yaml`

```yaml
gate:
  env:                                   # applied to every gate command
    JAVA_HOME: 'C:\Program Files\Java\jdk1.8.0_431'
    MAVEN_HOME: 'C:\tools\apache-maven-3.9.9'
  # Optional: replace the default "mvn -B -q test".
  # commands:
  #   - {name: unit, run: "mvn -B -q test", timeout_seconds: 900, required: true}
  # Faster variant, optional:
  #   - {name: unit, run: "mvn -B -q -o -T 1C test", timeout_seconds: 900, required: true}
```

- With `JAVA_HOME` in `gate.env`, its `bin` folder goes first on the gate's `PATH`, so `java` and `javac` are the JDK 8 ones even if another JDK is installed.
- Use single quotes for Windows paths in YAML, so backslashes are kept as they are.
- `-o` (offline) breaks when a dependency is missing from the local repository (`~/.m2/repository`); `-T 1C` (parallel build) can break plugins that are not thread-safe. That is why neither is in the default command.
- `target/**` is tolerated by default, so build output never counts as a change outside the task's scope. If you set `scope.tolerated_files`, the list replaces the default: keep `target/**` in it.

Also add `target/` to the project's `.gitignore`, so build output is never committed:

```
target/
```

## Flow with GitLab

Meister only uses local git (worktrees and integration); it never talks to GitLab. After the run:

```bash
git push -u origin <your-branch>
glab mr create --fill          # or open the merge request on the GitLab web page
```

## Not verified

The Windows CI leg now runs the gate on a real Maven project with the runner's Maven and JDK 8 (`tests/test_gate_maven_real.py`), which covers `mvn.cmd`, `PATHEXT` and Windows paths. What remains unverified is the owner's machine: the external Maven and the JDK installed there. Before a real plan, try a trivial task (for example, adding a comment to one class) with `meister orchestrate` and check that the gate runs Maven and passes.

To test batch jobs that call legacy sites without a staging environment, see [java-testing-without-staging.md](java-testing-without-staging.md).
