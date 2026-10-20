"""Tests for timeline project selector helper functions in timeline.html."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.timeline_js import run_js


def _js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------------------
# projectFromSearch(search)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("search", "expected"),
    [
        ("?project=alpha", "alpha"),
        ("project=alpha", "alpha"),
        ("?foo=1&project=alpha&bar=2", "alpha"),
        ("?project=46546a57a2dac472", "46546a57a2dac472"),
        ("?run=run_123&project=proj_xyz", "proj_xyz"),
    ],
)
def test_project_from_search_extracts_project_parameter(search: str, expected: str) -> None:
    assert run_js(f"projectFromSearch({_js(search)})") == expected


@pytest.mark.parametrize(
    "search",
    [
        "",
        "?",
        "?foo=bar",
        "?run=123",
        "?other=test&view=graph",
    ],
)
def test_project_from_search_without_parameter_returns_null(search: str) -> None:
    assert run_js(f"projectFromSearch({_js(search)})") is None


def test_project_from_search_null_and_undefined_returns_null() -> None:
    assert run_js("projectFromSearch(null)") is None
    assert run_js("projectFromSearch(undefined)") is None


@pytest.mark.parametrize(
    "search",
    [
        "?project=",
        "?project=&other=1",
        "?other=1&project=",
        "?project",
        "?project   ",
        "?project=   ",
        "?project=\t\n",
    ],
)
def test_project_from_search_empty_value_returns_null(search: str) -> None:
    assert run_js(f"projectFromSearch({_js(search)})") is None


@pytest.mark.parametrize(
    ("search", "expected"),
    [
        ("?project=hello%20world", "hello world"),
        ("?project=a%20b", "a b"),
        ("?project=a+b", "a b"),
        ("?project=a%26b", "a&b"),
        ("?project=a%26b%3Dc", "a&b=c"),
        ("?project=proj%2Fsub", "proj/sub"),
        ("?project=%C3%A9%C3%A0%C3%A7%C3%A3o", "éàção"),
        ("?project=p%3Fq%3D1", "p?q=1"),
    ],
)
def test_project_from_search_decodes_special_characters(search: str, expected: str) -> None:
    assert run_js(f"projectFromSearch({_js(search)})") == expected


# ---------------------------------------------------------------------------
# withProject(url, projectId)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "project_id", "expected"),
    [
        ("/api/runs", "alpha", "/api/runs?project=alpha"),
        ("/api/timeline", "proj_1", "/api/timeline?project=proj_1"),
        ("/timeline", "p1", "/timeline?project=p1"),
        ("/", "my_proj", "/?project=my_proj"),
    ],
)
def test_with_project_appends_to_url_without_query(url: str, project_id: str, expected: str) -> None:
    assert run_js(f"withProject({_js(url)}, {_js(project_id)})") == expected


@pytest.mark.parametrize(
    ("url", "project_id", "expected"),
    [
        ("/api/timelines?limit=100", "alpha", "/api/timelines?limit=100&project=alpha"),
        ("/api/timeline?run_id=abcdef", "alpha", "/api/timeline?run_id=abcdef&project=alpha"),
        ("/api/timeline?run_id=abc&limit=5", "alpha", "/api/timeline?run_id=abc&limit=5&project=alpha"),
        ("/api/runs?", "alpha", "/api/runs?project=alpha"),
        ("/api/timelines?limit=100&", "alpha", "/api/timelines?limit=100&project=alpha"),
    ],
)
def test_with_project_appends_to_url_with_query(url: str, project_id: str, expected: str) -> None:
    assert run_js(f"withProject({_js(url)}, {_js(project_id)})") == expected


@pytest.mark.parametrize(
    ("url", "project_id"),
    [
        ("/api/runs", None),
        ("/api/runs", ""),
        ("/api/timelines?limit=100", None),
        ("/api/timelines?limit=100", ""),
        ("/api/timeline?run_id=123", None),
        ("/api/timeline?run_id=123", ""),
    ],
)
def test_with_project_null_or_empty_returns_intact_url(url: str, project_id: str | None) -> None:
    assert run_js(f"withProject({_js(url)}, {_js(project_id)})") == url


def test_with_project_undefined_returns_intact_url() -> None:
    assert run_js("withProject('/api/runs', undefined)") == "/api/runs"


@pytest.mark.parametrize(
    ("url", "project_id", "expected"),
    [
        ("/api/runs", "hello world", "/api/runs?project=hello%20world"),
        ("/api/runs", "a&b=c", "/api/runs?project=a%26b%3Dc"),
        ("/api/runs", "a/b?c", "/api/runs?project=a%2Fb%3Fc"),
        ("/api/runs", "café", "/api/runs?project=caf%C3%A9"),
        ("/api/timelines?limit=10", "a b&c", "/api/timelines?limit=10&project=a%20b%26c"),
    ],
)
def test_with_project_encodes_special_characters(url: str, project_id: str, expected: str) -> None:
    assert run_js(f"withProject({_js(url)}, {_js(project_id)})") == expected


# ---------------------------------------------------------------------------
# projectOptions(projects, currentId)
# ---------------------------------------------------------------------------

SAMPLE_PROJECTS = [
    {"id": "aaa111", "name": "alpha", "path": "/path/alpha", "exists": True},
    {"id": "bbb222", "name": "beta", "path": "/path/beta", "exists": False},
    {"id": "ccc333", "name": "gamma", "path": "/path/gamma", "exists": True},
]


def test_project_options_maps_projects_with_missing_flag() -> None:
    options = run_js(f"projectOptions({_js(SAMPLE_PROJECTS)}, null)")
    assert options == [
        {"id": "aaa111", "name": "alpha", "path": "/path/alpha", "missing": False},
        {"id": "bbb222", "name": "beta", "path": "/path/beta", "missing": True},
        {"id": "ccc333", "name": "gamma", "path": "/path/gamma", "missing": False},
    ]


def test_project_options_preserves_order_as_received() -> None:
    shuffled = [SAMPLE_PROJECTS[2], SAMPLE_PROJECTS[0], SAMPLE_PROJECTS[1]]
    options = run_js(f"projectOptions({_js(shuffled)}, null)")
    assert [o["id"] for o in options] == ["ccc333", "aaa111", "bbb222"]


def test_project_options_existing_current_id_is_not_duplicated_and_keeps_order() -> None:
    options_mid = run_js(f"projectOptions({_js(SAMPLE_PROJECTS)}, 'bbb222')")
    assert [o["id"] for o in options_mid] == ["aaa111", "bbb222", "ccc333"]

    options_first = run_js(f"projectOptions({_js(SAMPLE_PROJECTS)}, 'aaa111')")
    assert [o["id"] for o in options_first] == ["aaa111", "bbb222", "ccc333"]


def test_project_options_unlisted_current_id_prepended_as_first_item() -> None:
    options = run_js(f"projectOptions({_js(SAMPLE_PROJECTS)}, 'ddd444')")
    assert len(options) == 4
    assert options[0] == {"id": "ddd444", "name": "ddd444", "path": "", "missing": False}
    assert [o["id"] for o in options] == ["ddd444", "aaa111", "bbb222", "ccc333"]


def test_project_options_unlisted_current_object_prepended_as_first_item() -> None:
    current = {"id": "ddd444", "name": "delta", "path": "/path/delta"}
    options = run_js(f"projectOptions({_js(SAMPLE_PROJECTS)}, {_js(current)})")
    assert len(options) == 4
    assert options[0] == {"id": "ddd444", "name": "delta", "path": "/path/delta", "missing": False}
    assert [o["id"] for o in options] == ["ddd444", "aaa111", "bbb222", "ccc333"]


def test_project_options_empty_and_null_inputs() -> None:
    assert run_js("projectOptions(null, null)") == []
    assert run_js("projectOptions(undefined, undefined)") == []
    assert run_js("projectOptions([], null)") == []
    assert run_js("projectOptions([], '')") == []


def test_project_options_empty_projects_with_current_id() -> None:
    options = run_js("projectOptions(null, 'standalone')")
    assert options == [{"id": "standalone", "name": "standalone", "path": "", "missing": False}]

    options2 = run_js("projectOptions([], 'standalone')")
    assert options2 == [{"id": "standalone", "name": "standalone", "path": "", "missing": False}]


def test_project_options_falls_back_to_path_or_id_when_name_is_absent() -> None:
    raw = [
        {"id": "p_path_only", "path": "/some/path/only", "exists": True},
        {"id": "p_id_only", "exists": True},
    ]
    options = run_js(f"projectOptions({_js(raw)}, null)")
    assert options == [
        {"id": "p_path_only", "name": "/some/path/only", "path": "/some/path/only", "missing": False},
        {"id": "p_id_only", "name": "p_id_only", "path": "", "missing": False},
    ]


def test_project_options_missing_when_exists_is_false() -> None:
    raw = [
        {"id": "p_missing", "name": "Missing", "path": "/m", "exists": False},
        {"id": "p_present", "name": "Present", "path": "/p", "exists": True},
        {"id": "p_default", "name": "Default", "path": "/d"},
    ]
    options = run_js(f"projectOptions({_js(raw)}, null)")
    assert options == [
        {"id": "p_missing", "name": "Missing", "path": "/m", "missing": True},
        {"id": "p_present", "name": "Present", "path": "/p", "missing": False},
        {"id": "p_default", "name": "Default", "path": "/d", "missing": False},
    ]
