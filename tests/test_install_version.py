"""Hermetic tests for version selection in bin/install.sh."""

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SCRIPT = REPO_ROOT / "bin" / "install.sh"


def _source_script(command, *, env=None, args=()):
    environment = os.environ.copy()
    environment["MEISTER_INSTALL_SOURCE_ONLY"] = "1"
    if env:
        environment.update(env)
    return subprocess.run(
        ["/bin/bash", "-c", f'source "$1"; shift; {command}', "bash", str(INSTALL_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=environment,
    )


@pytest.mark.parametrize("version", ["main", "latest", "v0.9.0", "v1.2.3-rc.1"])
def test_validate_version_ref_accepts_supported_refs(version):
    result = _source_script('validate_version_ref "$1"', args=(version,))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "version",
    ["", "0.9.0", "v0.9", "v1.2.3;rm -rf /", "$(x)", "-x", "feature/x", "v1.2.3 beta"],
)
def test_validate_version_ref_rejects_unsupported_refs(version):
    result = _source_script('validate_version_ref "$1"', args=(version,))
    assert result.returncode == 2
    assert "versão inválida" in result.stderr


def test_parse_install_args_forms_precedence_and_default():
    cases = [
        (["--version", "v0.9.0"], {}, "v0.9.0"),
        (["--version=v0.9.0"], {}, "v0.9.0"),
        ([], {"MEISTER_VERSION": "v1.2.3"}, "v1.2.3"),
        (["--version", "v0.9.0"], {"MEISTER_VERSION": "v1.2.3"}, "v0.9.0"),
        ([], {}, "main"),
    ]
    for args, env, expected in cases:
        result = _source_script(
            'parse_install_args "$@" || exit $?; printf "%s" "$INSTALL_VERSION"',
            env=env,
            args=args,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == expected


def test_parse_install_args_help_unknown_and_missing_version():
    help_result = _source_script(
        'parse_install_args "$@" || exit $?; test "$INSTALL_HELP" = 1',
        args=("--help",),
    )
    assert help_result.returncode == 0

    unknown = _source_script('parse_install_args "$@"', args=("--other",))
    assert unknown.returncode == 2
    assert "argumento desconhecido" in unknown.stderr

    missing = _source_script('parse_install_args "$@"', args=("--version",))
    assert missing.returncode == 2
    assert "--version exige" in missing.stderr

    empty_env = _source_script('parse_install_args "$@"', env={"MEISTER_VERSION": ""})
    assert empty_env.returncode == 2
    assert "versão inválida" in empty_env.stderr


def _make_install_doubles(tmp_path):
    bin_dir = tmp_path / "doubles"
    bin_dir.mkdir()
    git_log = tmp_path / "git.log"
    git_script = bin_dir / "git"
    git_script.write_text(
        """#!/bin/bash
printf '%s\\n' "$*" >> "$GIT_LOG"
if [ "$1" = "ls-remote" ]; then
    if [ "${NO_REMOTE_TAGS:-0}" = "1" ]; then exit 0; fi
    printf '0123456789abcdef refs/tags/v2.4.1\\n'
    exit 0
fi
if [ "$1" = "clone" ]; then
    for destination do :; done
    mkdir -p "$destination/.git" "$destination/bin"
    : > "$destination/setup.py"
    exit 0
fi
if [ "$1" = "-C" ] && [ "${FAIL_CHECKOUT:-0}" = "1" ] && [ "$3" = "checkout" ]; then
    exit 1
fi
exit 0
""",
        encoding="utf-8",
    )
    git_script.chmod(0o755)

    python_script = bin_dir / "python3"
    python_script.write_text(
        """#!/bin/bash
if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
    mkdir -p "$3/bin"
    cat > "$3/bin/pip" <<'PIP'
#!/bin/bash
exit 0
PIP
    cat > "$3/bin/meister" <<'MEISTER'
#!/bin/bash
case "$1" in
    --help) echo "MeisterRouter CLI" ;;
    --version) echo "meister 0.9.0 (commit test)" ;;
esac
MEISTER
    chmod +x "$3/bin/pip" "$3/bin/meister"
fi
""",
        encoding="utf-8",
    )
    python_script.chmod(0o755)

    herdr_script = bin_dir / "herdr"
    herdr_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    herdr_script.chmod(0o755)
    return bin_dir, git_log


def _install_env(tmp_path, bin_dir, git_log, **extra):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
            "GIT_LOG": str(git_log),
            "MEISTER_ALLOW_NO_HERDR": "1",
            "MEISTER_SKIP_SETUP": "1",
        }
    )
    env.update({key: str(value) for key, value in extra.items()})
    return env


def _run_piped_install(args, env, *, cwd=REPO_ROOT):
    return subprocess.run(
        ["bash", "-s", "--", *args],
        input=INSTALL_SCRIPT.read_text(encoding="utf-8"),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
    )


def test_first_clone_pins_tag_and_default_stays_main(tmp_path):
    bin_dir, git_log = _make_install_doubles(tmp_path)
    result = _run_piped_install(
        ["--version", "v0.9.0"], _install_env(tmp_path, bin_dir, git_log)
    )
    assert result.returncode == 0, result.stderr
    clone_call = git_log.read_text(encoding="utf-8")
    assert "clone --depth=1 --branch v0.9.0" in clone_call
    assert "• Versão: meister 0.9.0" in result.stdout

    default_home = tmp_path / "default-home"
    default_home.mkdir()
    default_log = tmp_path / "default-git.log"
    default_env = _install_env(tmp_path, bin_dir, default_log)
    default_env["HOME"] = str(default_home)
    default_result = _run_piped_install([], default_env)
    assert default_result.returncode == 0, default_result.stderr
    assert "clone --depth=1 https://" in default_log.read_text(encoding="utf-8")
    assert "--branch" not in default_log.read_text(encoding="utf-8")


def test_latest_resolves_remote_tag_before_clone(tmp_path):
    bin_dir, git_log = _make_install_doubles(tmp_path)
    result = _run_piped_install(
        ["--version=latest"], _install_env(tmp_path, bin_dir, git_log)
    )
    assert result.returncode == 0, result.stderr
    calls = git_log.read_text(encoding="utf-8")
    assert "ls-remote --tags --refs --sort=-v:refname" in calls
    assert "clone --depth=1 --branch v2.4.1" in calls


def test_latest_without_remote_tags_fails_clearly(tmp_path):
    bin_dir, git_log = _make_install_doubles(tmp_path)
    result = _run_piped_install(
        ["--version", "latest"],
        _install_env(tmp_path, bin_dir, git_log, NO_REMOTE_TAGS=1),
    )
    assert result.returncode == 1
    assert "não foi possível encontrar uma tag" in result.stderr
    assert "clone" not in git_log.read_text(encoding="utf-8")


def test_invalid_version_fails_before_any_git_clone(tmp_path):
    bin_dir, git_log = _make_install_doubles(tmp_path)
    result = _run_piped_install(
        ["--version", "feature/x"], _install_env(tmp_path, bin_dir, git_log)
    )
    assert result.returncode == 2
    assert "versão inválida" in result.stderr
    assert not git_log.exists()


def test_existing_clone_fetches_and_checks_out_tag_without_pull(tmp_path):
    bin_dir, git_log = _make_install_doubles(tmp_path)
    home = tmp_path / "home"
    repo = home / ".local/share/meisterrouter"
    (repo / ".git").mkdir(parents=True)
    (repo / "setup.py").write_text("", encoding="utf-8")

    result = _run_piped_install(
        ["--version", "v0.9.0"], _install_env(tmp_path, bin_dir, git_log)
    )
    assert result.returncode == 0, result.stderr
    calls = git_log.read_text(encoding="utf-8")
    assert "fetch --depth=1 origin refs/tags/v0.9.0:refs/tags/v0.9.0" in calls
    assert "checkout --quiet v0.9.0" in calls
    assert "pull" not in calls


def test_failed_existing_clone_checkout_returns_error(tmp_path):
    bin_dir, git_log = _make_install_doubles(tmp_path)
    home = tmp_path / "home"
    repo = home / ".local/share/meisterrouter"
    (repo / ".git").mkdir(parents=True)
    (repo / "setup.py").write_text("", encoding="utf-8")

    result = _run_piped_install(
        ["--version", "v0.9.0"],
        _install_env(tmp_path, bin_dir, git_log, FAIL_CHECKOUT=1),
    )
    assert result.returncode == 1
    assert "não foi possível selecionar a tag" in result.stderr
    assert "pull" not in git_log.read_text(encoding="utf-8")


def test_local_clone_is_not_checked_out_or_cloned_for_fixed_version(tmp_path):
    bin_dir, git_log = _make_install_doubles(tmp_path)
    local_repo = tmp_path / "local-repo"
    (local_repo / "bin").mkdir(parents=True)
    (local_repo / "setup.py").write_text("", encoding="utf-8")
    (local_repo / "bin/install.sh").write_text(
        INSTALL_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8"
    )
    venv_bin = local_repo / ".venv/bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "pip").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    meister = venv_bin / "meister"
    meister.write_text(
        '#!/bin/sh\n[ "$1" = "--help" ] && echo "MeisterRouter CLI"\n'
        '[ "$1" = "--version" ] && echo "meister test"\n',
        encoding="utf-8",
    )
    (venv_bin / "pip").chmod(0o755)
    meister.chmod(0o755)

    env = _install_env(tmp_path, bin_dir, git_log)
    result = subprocess.run(
        ["bash", str(local_repo / "bin/install.sh"), "--version", "v0.9.0"],
        capture_output=True,
        text=True,
        cwd=local_repo,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "a versão fixa só vale para a instalação por curl" in result.stdout
    assert str(local_repo) in result.stdout
    assert not git_log.exists()


def test_source_only_does_not_run_installation():
    result = _source_script('printf "source-returned"')
    assert result.returncode == 0, result.stderr
    assert result.stdout == "source-returned"


def test_install_script_help_and_invalid_version_exit_codes(tmp_path):
    bin_dir, git_log = _make_install_doubles(tmp_path)
    env = _install_env(tmp_path, bin_dir, git_log)

    help_result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert help_result.returncode == 0
    assert "Uso: install.sh" in help_result.stdout

    invalid_result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT), "--version", "abc"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert invalid_result.returncode == 2
    assert "versão inválida" in invalid_result.stderr
    assert not git_log.exists()
