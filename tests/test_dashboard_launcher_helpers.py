from __future__ import annotations

from pathlib import Path

import pytest

from meister.dashboard.launcher import (
    build_app_argv,
    default_profile_dir,
    find_chromium,
    has_display,
)


@pytest.mark.parametrize(
    ("platform", "available", "expected"),
    [
        (
            "linux",
            {"brave-browser": "/bin/brave", "microsoft-edge": "/bin/edge",
             "chromium": "/bin/chromium", "google-chrome": "/bin/chrome"},
            ["/bin/chrome"],
        ),
        (
            "linux",
            {"chromium": "/bin/chromium", "microsoft-edge": "/bin/edge",
             "brave-browser": "/bin/brave"},
            ["/bin/chromium"],
        ),
        (
            "linux",
            {"microsoft-edge": "/bin/edge", "brave-browser": "/bin/brave"},
            ["/bin/edge"],
        ),
        ("linux", {"brave-browser": "/bin/brave"}, ["/bin/brave"]),
    ],
)
def test_find_chromium_linux_preference_order(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    available: dict[str, str],
    expected: list[str],
) -> None:
    from meister.dashboard import launcher

    checked: list[str] = []

    def fake_which(executable: str) -> str | None:
        checked.append(executable)
        return available.get(executable)

    monkeypatch.setattr(launcher.shutil, "which", fake_which)

    assert find_chromium(platform, {}) == expected
    if expected == ["/bin/chrome"]:
        assert checked == ["google-chrome"]


def test_find_chromium_linux_returns_none_when_no_supported_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from meister.dashboard import launcher

    monkeypatch.setattr(launcher.shutil, "which", lambda executable: None)

    assert find_chromium("linux", {}) is None


def test_find_chromium_ignores_firefox(monkeypatch: pytest.MonkeyPatch) -> None:
    from meister.dashboard import launcher

    checked: list[str] = []

    def fake_which(executable: str) -> str | None:
        checked.append(executable)
        return "/usr/bin/firefox" if executable == "firefox" else None

    monkeypatch.setattr(launcher.shutil, "which", fake_which)

    assert find_chromium("linux", {}) is None
    assert "firefox" not in checked


@pytest.mark.parametrize(
    ("available", "expected"),
    [
        (
            {"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"},
            ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
        ),
        (
            {
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            },
            ["/Applications/Chromium.app/Contents/MacOS/Chromium"],
        ),
        (
            {
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            },
            ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"],
        ),
        (
            {"/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"},
            ["/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"],
        ),
    ],
)
def test_find_chromium_macos_preference_order(
    monkeypatch: pytest.MonkeyPatch,
    available: set[str],
    expected: list[str],
) -> None:
    from meister.dashboard import launcher

    monkeypatch.setattr(launcher.Path, "is_file", lambda path: path.as_posix() in available)

    found = find_chromium("darwin", {})

    assert found is not None
    assert [Path(p).as_posix() for p in found] == expected


def test_find_chromium_macos_returns_none_when_no_supported_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from meister.dashboard import launcher

    monkeypatch.setattr(launcher.Path, "is_file", lambda path: False)

    assert find_chromium("darwin", {}) is None


@pytest.mark.parametrize(
    ("available", "expected"),
    [
        (
            {r"C:\Program Files\Google\Chrome\Application\chrome.exe"},
            [r"C:\Program Files\Google\Chrome\Application\chrome.exe"],
        ),
        (
            {
                r"C:\Program Files\Chromium\Application\chrome.exe",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            },
            [r"C:\Program Files\Chromium\Application\chrome.exe"],
        ),
        (
            {
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            },
            [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"],
        ),
        (
            {r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"},
            [r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"],
        ),
    ],
)
def test_find_chromium_windows_preference_order(
    monkeypatch: pytest.MonkeyPatch,
    available: set[str],
    expected: list[str],
) -> None:
    from meister.dashboard import launcher

    monkeypatch.setattr(launcher.os.path, "isfile", lambda path: path in available)

    assert find_chromium("win32", {}) == expected


def test_find_chromium_windows_returns_none_when_no_supported_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from meister.dashboard import launcher

    monkeypatch.setattr(launcher.os.path, "isfile", lambda path: False)

    assert find_chromium("win32", {}) is None


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"DISPLAY": ":0"}, True),
        ({"WAYLAND_DISPLAY": "wayland-0"}, True),
        ({"DISPLAY": ":0", "SSH_TTY": "/dev/pts/0"}, False),
        ({"WAYLAND_DISPLAY": "wayland-0", "CI": "true"}, False),
        ({}, False),
        ({"DISPLAY": "", "WAYLAND_DISPLAY": ""}, False),
    ],
)
def test_has_display_on_linux(env: dict[str, str], expected: bool) -> None:
    assert has_display("linux", env) is expected


@pytest.mark.parametrize(
    "env",
    [
        {"SSH_CLIENT": "127.0.0.1 12345 22", "DISPLAY": ":0"},
        {"CI": "true", "WAYLAND_DISPLAY": "wayland-0"},
    ],
)
def test_has_display_rejects_ssh_and_ci_on_all_platforms(
    env: dict[str, str],
) -> None:
    assert not has_display("darwin", env)
    assert not has_display("win32", env)


def test_has_display_is_true_on_non_linux_without_ssh_or_ci() -> None:
    assert has_display("darwin", {})
    assert has_display("win32", {})


def test_build_app_argv() -> None:
    assert build_app_argv(
        ["/usr/bin/chromium"],
        "http://127.0.0.1:5050",
        "/tmp/browser-profile",
        1280,
        800,
    ) == [
        "/usr/bin/chromium",
        "--app=http://127.0.0.1:5050",
        "--user-data-dir=/tmp/browser-profile",
        "--window-size=1280,800",
        "--no-first-run",
    ]


def test_default_profile_dir_uses_home_without_creating_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    profile = default_profile_dir()

    assert Path(profile).resolve() == (home / ".meister" / "browser-profile").resolve()
    assert not home.exists()
