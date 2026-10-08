from __future__ import annotations

import ntpath
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Union

from meister.dashboard.state import is_alive, read_state


@dataclass
class Outcome:
    kind: str
    url: Optional[str] = None



_LINUX_EXECUTABLES = (
    ("google-chrome", "google-chrome-stable", "google-chrome-beta", "google-chrome-unstable"),
    ("chromium", "chromium-browser"),
    ("microsoft-edge", "microsoft-edge-stable", "microsoft-edge-beta", "microsoft-edge-dev"),
    ("brave-browser", "brave", "brave-browser-beta"),
)

_MACOS_APPS = (
    ("Google Chrome", "Google Chrome"),
    ("Chromium", "Chromium"),
    ("Microsoft Edge", "Microsoft Edge"),
    ("Brave Browser", "Brave Browser"),
)


def find_chromium(platform: str, env: Mapping[str, str]) -> Optional[list[str]]:
    """Return the first supported Chromium-based browser executable for this platform."""
    if platform.startswith("linux"):
        for browser_executables in _LINUX_EXECUTABLES:
            for executable in browser_executables:
                found = shutil.which(executable)
                if found:
                    return [found]
        return None

    if platform == "darwin":
        mac_roots = [Path("/Applications")]
        home = env.get("HOME")
        if home:
            mac_roots.append(Path(home) / "Applications")
        for app_name, executable in _MACOS_APPS:
            for root in mac_roots:
                binary = root / f"{app_name}.app" / "Contents" / "MacOS" / executable
                if binary.is_file():
                    return [str(binary)]
        return None

    if platform == "win32":
        program_files = env.get("PROGRAMFILES", r"C:\Program Files")
        program_files_x86 = env.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        local_app_data = env.get("LOCALAPPDATA")
        windows_roots = [program_files, program_files_x86]
        browser_paths = (
            ("Google", "Chrome", "Application", "chrome.exe"),
            ("Chromium", "Application", "chrome.exe"),
            ("Microsoft", "Edge", "Application", "msedge.exe"),
            ("BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
        )
        for parts in browser_paths:
            candidates = [ntpath.join(root, *parts) for root in windows_roots]
            if local_app_data:
                candidates.append(ntpath.join(local_app_data, *parts))
            for candidate in candidates:
                if os.path.isfile(candidate):
                    return [candidate]
        return None

    return None


def has_display(platform: str, env: Mapping[str, str]) -> bool:
    """Return whether opening a local browser is appropriate in this environment."""
    if any(env.get(key) for key in ("SSH_CLIENT", "SSH_TTY", "SSH_CONNECTION")):
        return False
    disabled_values = {"", "0", "false", "no", "off"}
    if any(
        value.strip().lower() not in disabled_values
        for key in ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_URL", "BUILD_NUMBER")
        if (value := env.get(key, "")) is not None
    ):
        return False
    if platform.startswith("linux"):
        return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))
    return True


def build_app_argv(
    binary: list[str],
    url: str,
    profile_dir: str,
    width: int,
    height: int,
) -> list[str]:
    """Build Chromium app-mode arguments without launching a process."""
    # Chromium app mode accepts these flags; --user-data-dir enables a shared profile.
    return [
        *binary,
        f"--app={url}",
        f"--user-data-dir={profile_dir}",
        f"--window-size={width},{height}",
        "--no-first-run",
    ]


def default_profile_dir() -> str:
    """Return the shared browser profile location without creating it."""
    return os.path.expanduser("~/.meister/browser-profile")


def _format_timeline_url(base_url: str) -> str:
    cleaned = base_url.rstrip("/")
    if cleaned.endswith("/timeline"):
        return cleaned
    return f"{cleaned}/timeline"


def _parse_config(config: Any) -> tuple[str, int, int, int]:
    open_mode = "auto"
    idle_exit_minutes = 30
    width = 1280
    height = 800

    dashboard_cfg = getattr(config, "dashboard", config)
    if isinstance(dashboard_cfg, dict):
        open_mode = dashboard_cfg.get("open", open_mode)
        idle_exit_minutes = dashboard_cfg.get("idle_exit_minutes", idle_exit_minutes)
        window = dashboard_cfg.get("window", {})
        if isinstance(window, dict):
            width = window.get("width", width)
            height = window.get("height", height)
        else:
            width = getattr(window, "width", width)
            height = getattr(window, "height", height)
    elif dashboard_cfg is not None:
        open_mode = getattr(dashboard_cfg, "open", open_mode)
        idle_exit_minutes = getattr(dashboard_cfg, "idle_exit_minutes", idle_exit_minutes)
        window = getattr(dashboard_cfg, "window", None)
        if window is not None:
            width = getattr(window, "width", width)
            height = getattr(window, "height", height)

    return str(open_mode), int(idle_exit_minutes), int(width), int(height)


def ensure_dashboard(
    project_root: Union[str, Path],
    config: Any = None,
    *,
    open_window: bool = True,
) -> Outcome:
    """Ensure the project's dashboard server is running and open the timeline view."""
    try:
        project_root = Path(project_root)
        open_mode, idle_exit_minutes, width, height = _parse_config(config)

        if not open_window or open_mode == "never":
            return Outcome(kind="disabled", url=None)

        start_time = time.monotonic()
        deadline = start_time + 2.0

        state = read_state(project_root)
        now = datetime.now(timezone.utc)

        server_alive = False
        window_alive = False
        if state is not None and is_alive(state, now=now, max_age_s=float("inf")):
            server_alive = True
            window_alive = is_alive(state, now=now, max_age_s=5.0)

        if not server_alive:
            meister_dir = project_root / ".meister"
            meister_dir.mkdir(parents=True, exist_ok=True)
            lock_path = meister_dir / "dashboard.lock"

            have_lock = False
            lock_fd = None

            while time.monotonic() < deadline:
                try:
                    lock_fd = os.open(
                        str(lock_path),
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    )
                    have_lock = True
                    break
                except FileExistsError:
                    try:
                        stat = lock_path.stat()
                        if time.time() - stat.st_mtime > 5.0:
                            lock_path.unlink(missing_ok=True)
                            continue
                    except OSError:
                        pass

                    time.sleep(0.02)
                    st = read_state(project_root)
                    if st is not None and is_alive(st, now=datetime.now(timezone.utc), max_age_s=float("inf")):
                        state = st
                        server_alive = True
                        break
                except OSError:
                    time.sleep(0.02)

            if have_lock and lock_fd is not None:
                try:
                    os.close(lock_fd)
                    st = read_state(project_root)
                    if st is not None and is_alive(st, now=datetime.now(timezone.utc), max_age_s=float("inf")):
                        state = st
                        server_alive = True
                    else:
                        cmd = [
                            sys.executable,
                            "-m",
                            "meister.cli",
                            "dashboard",
                            "--port",
                            "0",
                            "--idle-exit-minutes",
                            str(idle_exit_minutes),
                        ]
                        try:
                            subprocess.Popen(
                                cmd,
                                cwd=str(project_root),
                                start_new_session=True,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL,
                            )
                        except Exception:
                            return Outcome(kind="failed", url=None)

                        while time.monotonic() < deadline:
                            st = read_state(project_root)
                            if st is not None and is_alive(st, now=datetime.now(timezone.utc), max_age_s=float("inf")):
                                state = st
                                server_alive = True
                                break
                            time.sleep(0.02)
                finally:
                    try:
                        lock_path.unlink(missing_ok=True)
                    except OSError:
                        pass

            if not server_alive or state is None:
                return Outcome(kind="failed", url=None)

            window_alive = False

        if state is None:
            return Outcome(kind="failed", url=None)

        timeline_url = _format_timeline_url(state.url)

        if not has_display(sys.platform, os.environ):
            return Outcome(kind="url_only", url=timeline_url)

        chromium_bin = find_chromium(sys.platform, os.environ) if open_mode in ("auto", "app") else None

        if window_alive:
            kind = "window" if chromium_bin is not None else "tab"
            return Outcome(kind=kind, url=timeline_url)

        if chromium_bin is not None and open_mode in ("auto", "app"):
            profile_dir = default_profile_dir()
            argv = build_app_argv(
                chromium_bin,
                timeline_url,
                profile_dir,
                width=width,
                height=height,
            )
            try:
                subprocess.Popen(
                    argv,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                )
                return Outcome(kind="window", url=timeline_url)
            except Exception:
                return Outcome(kind="failed", url=None)
        else:
            try:
                webbrowser.open(timeline_url)
                return Outcome(kind="tab", url=timeline_url)
            except Exception:
                return Outcome(kind="failed", url=None)

    except Exception:
        return Outcome(kind="failed", url=None)

