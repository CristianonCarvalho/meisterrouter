import os
import re
import subprocess
from pathlib import Path

from tests.platform_marks import posix_only


pytestmark = posix_only

REPO_ROOT = Path(__file__).resolve().parent.parent
WRAPPER_PATH = REPO_ROOT / "bin" / "herdr-meister.sh"


def _make_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _base_env(tmp_path: Path) -> dict[str, str]:
    return {
        "HOME": str(tmp_path / "home"),
        "PATH": "/usr/bin:/bin",
        "MEISTER_OPT_DIR": str(tmp_path / "opt_empty"),
        "MEISTER_USR_DIR": str(tmp_path / "usr_empty"),
    }


def test_herdr_wrapper_syntax_and_permissions():
    assert WRAPPER_PATH.exists(), "bin/herdr-meister.sh must exist"
    assert os.access(WRAPPER_PATH, os.X_OK), "bin/herdr-meister.sh must be executable"

    # Syntax validation with POSIX sh
    result = subprocess.run(["sh", "-n", str(WRAPPER_PATH)], capture_output=True, text=True)
    assert result.returncode == 0, f"sh -n failed: {result.stderr}"

    content = WRAPPER_PATH.read_text(encoding="utf-8")
    assert "[[" not in content, "Must not use bash [[ conditional"
    assert not re.search(r"\blocal\s+", content), "Must not use non-POSIX 'local' keyword"
    assert not re.search(r"\b\w+=\(", content), "Must not use bash arrays"
    assert "$'" not in content, "Must not use bash $'...' strings"


def test_herdr_wrapper_meister_in_path(tmp_path: Path):
    mock_bin = tmp_path / "bin"
    args_file = tmp_path / "args.txt"
    mock_meister = mock_bin / "meister"

    _make_executable(
        mock_meister,
        f"""#!/bin/sh
for arg in "$@"; do
    printf "%s\\n" "$arg" >> "{args_file}"
done
exit 3
""",
    )

    env = _base_env(tmp_path)
    env["PATH"] = f"{mock_bin}:/usr/bin:/bin"

    test_args = ["daemon", "--start", "hello world", "single'quote", "dollar$var"]
    proc = subprocess.run(
        ["sh", str(WRAPPER_PATH), *test_args],
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 3
    recorded = args_file.read_text(encoding="utf-8").splitlines()
    assert recorded == test_args


def test_herdr_wrapper_meister_in_home_local_bin(tmp_path: Path):
    home = tmp_path / "home"
    local_bin = home / ".local" / "bin"
    mock_meister = local_bin / "meister"
    args_file = tmp_path / "args.txt"

    _make_executable(
        mock_meister,
        f"""#!/bin/sh
printf "%s\\n" "$1" > "{args_file}"
exit 3
""",
    )

    env = _base_env(tmp_path)
    # Ensure mock_bin is NOT in PATH
    env["PATH"] = "/usr/bin:/bin"

    proc = subprocess.run(
        ["sh", str(WRAPPER_PATH), "herdr-action", "verify"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 3
    assert args_file.read_text(encoding="utf-8").strip() == "herdr-action"


def test_herdr_wrapper_meister_bin_env_priority(tmp_path: Path):
    path_bin = tmp_path / "path_bin"
    override_bin = tmp_path / "override_bin"

    _make_executable(path_bin / "meister", "#!/bin/sh\nexit 1\n")
    _make_executable(override_bin / "custom-meister", "#!/bin/sh\nexit 3\n")

    env = _base_env(tmp_path)
    env["PATH"] = f"{path_bin}:/usr/bin:/bin"
    env["MEISTER_BIN"] = str(override_bin / "custom-meister")

    proc = subprocess.run(
        ["sh", str(WRAPPER_PATH), "classify"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 3


def test_herdr_wrapper_missing_in_action(tmp_path: Path):
    herdr_bin = tmp_path / "herdr_bin" / "herdr"
    herdr_log = tmp_path / "herdr_call.log"

    _make_executable(
        herdr_bin,
        f"""#!/bin/sh
for arg in "$@"; do
    printf "%s\\n" "$arg" >> "{herdr_log}"
done
exit 0
""",
    )

    env = _base_env(tmp_path)
    env["HERDR_BIN_PATH"] = str(herdr_bin)

    proc = subprocess.run(
        ["sh", str(WRAPPER_PATH), "herdr-action", "orchestrate"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 127
    assert (
        "curl -fsSL https://raw.githubusercontent.com/CristianonCarvalho/meisterrouter/main/bin/install.sh | bash"
        in proc.stderr
    )
    assert "(see https://github.com/CristianonCarvalho/meisterrouter#-install)" in proc.stderr

    recorded = herdr_log.read_text(encoding="utf-8").splitlines()
    assert recorded == [
        "notification",
        "show",
        "MeisterRouter",
        "--body",
        "meister CLI not found. Install: curl -fsSL https://raw.githubusercontent.com/CristianonCarvalho/meisterrouter/main/bin/install.sh | bash",
    ]


def test_herdr_wrapper_missing_in_startup(tmp_path: Path):
    herdr_bin = tmp_path / "herdr_bin" / "herdr"
    herdr_log = tmp_path / "herdr_call.log"

    _make_executable(
        herdr_bin,
        f"""#!/bin/sh
printf "%s\\n" "$*" >> "{herdr_log}"
exit 0
""",
    )

    env = _base_env(tmp_path)
    env["HERDR_BIN_PATH"] = str(herdr_bin)
    env["HERDR_PLUGIN_EVENT"] = "startup"

    proc = subprocess.run(
        ["sh", str(WRAPPER_PATH), "daemon", "--start"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0
    assert (
        "curl -fsSL https://raw.githubusercontent.com/CristianonCarvalho/meisterrouter/main/bin/install.sh | bash"
        in proc.stderr
    )
    assert not herdr_log.exists()


def test_herdr_wrapper_missing_herdr_fails_or_nonexistent(tmp_path: Path):
    env = _base_env(tmp_path)
    env["HERDR_BIN_PATH"] = "/nonexistent/herdr"

    proc = subprocess.run(
        ["sh", str(WRAPPER_PATH), "herdr-action", "classify"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 127
    assert "MeisterRouter: the 'meister' CLI was not found" in proc.stderr

    # Also test when HERDR_BIN_PATH returns non-zero error code
    failing_herdr = tmp_path / "failing_herdr"
    _make_executable(failing_herdr, "#!/bin/sh\nexit 1\n")
    env["HERDR_BIN_PATH"] = str(failing_herdr)

    proc = subprocess.run(
        ["sh", str(WRAPPER_PATH), "herdr-action", "classify"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 127
    assert "MeisterRouter: the 'meister' CLI was not found" in proc.stderr


def test_herdr_wrapper_interactive_terminal(tmp_path: Path):
    import pty
    import select

    master, slave = pty.openpty()
    env = _base_env(tmp_path)

    proc = subprocess.Popen(
        ["sh", str(WRAPPER_PATH), "dashboard", "--tui"],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        close_fds=True,
        env=env,
    )
    os.close(slave)

    output = b""
    while b"Press Enter to close..." not in output:
        r, _, _ = select.select([master], [], [], 3.0)
        if not r:
            break
        try:
            chunk = os.read(master, 1024)
            if not chunk:
                break
            output += chunk
        except OSError:
            break

    assert b"Press Enter to close..." in output, f"Prompt not received. Got: {output!r}"

    # Send newline to unblock interactive read
    os.write(master, b"\n")
    proc.wait(timeout=3.0)
    os.close(master)

    assert proc.returncode == 127


def test_herdr_wrapper_non_interactive_does_not_wait(tmp_path: Path):
    env = _base_env(tmp_path)

    proc = subprocess.run(
        ["sh", str(WRAPPER_PATH), "dashboard", "--tui"],
        env=env,
        capture_output=True,
        text=True,
        timeout=3.0,
    )

    assert proc.returncode == 127
    assert "Press Enter to close..." not in proc.stderr
    assert "Press Enter to close..." not in proc.stdout


def test_herdr_wrapper_opt_homebrew_fallback(tmp_path: Path):
    opt_dir = tmp_path / "opt"
    mock_meister = opt_dir / "bin" / "meister"
    args_file = tmp_path / "args.txt"

    _make_executable(
        mock_meister,
        f"""#!/bin/sh
printf "%s\\n" "$1" > "{args_file}"
exit 3
""",
    )

    env = _base_env(tmp_path)
    env["MEISTER_OPT_DIR"] = str(opt_dir)

    proc = subprocess.run(
        ["sh", str(WRAPPER_PATH), "dashboard", "--tui"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 3
    assert args_file.read_text(encoding="utf-8").strip() == "dashboard"
