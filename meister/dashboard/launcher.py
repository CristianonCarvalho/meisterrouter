from __future__ import annotations

import ntpath
import os
import shutil
from pathlib import Path
from typing import Mapping, Optional


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
