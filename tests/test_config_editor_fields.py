import pytest

from meister.config_editor import (
    LaneDocument,
    LaneEdit,
    apply_lanes,
    load_effective_lanes,
    validate_lanes,
)

CONFIG = """\
# topo
workers:
  # vias
  tier_order:
    - name: lane_a  # primeira
      harness: codex
      model: gpt-x
      cost_per_m_tokens: 0.2  # custo
      max_retries: 2
      eligible_classes: [SMALL, MEDIUM]  # classes
      best_for:
        - refactors
    - name: lane_b
      harness: claude
      model: sonnet
"""


def _document(tmp_path) -> LaneDocument:
    path = tmp_path / "meister.config.yaml"
    path.write_bytes(CONFIG.encode("utf-8"))
    return load_effective_lanes(str(path))


def _request(**changes) -> list:
    lane_a = {"name": "lane_a", "harness": "codex", "model": "gpt-x"}
    lane_a.update(changes)
    return [lane_a, {"name": "lane_b", "harness": "claude", "model": "sonnet"}]


def _paths(document, lanes) -> list:
    return [issue.path for issue in validate_lanes(document, lanes) if issue.level == "error"]


def _save(tmp_path, **changes) -> tuple:
    document = _document(tmp_path)
    result = apply_lanes(document, _request(**changes), document.stamp)
    return result, (tmp_path / "meister.config.yaml").read_text(encoding="utf-8")


def _lane_a(result) -> dict:
    return result.lanes[0]


@pytest.mark.parametrize(
    "field_name, value",
    [
        ("cost_per_m_tokens", 1.5),
        ("credit_usd", 20),
        ("max_retries", 5),
        ("idle_timeout_seconds", 90),
        ("max_runtime_seconds", 1800.5),
        ("eligible_classes", ["HIGH"]),
        ("best_for", ["testes", "docs"]),
    ],
)
def test_new_field_is_written_and_reread_keeping_comments(tmp_path, field_name, value):
    result, text = _save(tmp_path, **{field_name: value})

    assert _lane_a(result)[field_name] == value
    for comment in ("# topo", "# vias", "# primeira"):
        assert comment in text
    assert load_effective_lanes(str(tmp_path / "meister.config.yaml")).lanes[0][field_name] == value


def test_unchanged_values_keep_inline_comments(tmp_path):
    result, text = _save(tmp_path, cost_per_m_tokens=0.2, max_retries=2, eligible_classes=["SMALL", "MEDIUM"])

    assert "cost_per_m_tokens: 0.2  # custo" in text
    assert "# classes" in text
    assert _lane_a(result)["best_for"] == ["refactors"] or "best_for" not in _lane_a(result)


@pytest.mark.parametrize(
    "field_name, value",
    [
        ("cost_per_m_tokens", None),
        ("max_retries", None),
        ("eligible_classes", []),
        ("best_for", []),
        ("best_for", None),
    ],
)
def test_null_or_empty_list_removes_the_key(tmp_path, field_name, value):
    result, text = _save(tmp_path, **{field_name: value})

    assert field_name not in _lane_a(result)
    assert f"{field_name}:" not in text.split("lane_b")[0]
    assert "# primeira" in text


def test_omitted_new_fields_keep_current_values(tmp_path):
    document = _document(tmp_path)

    result = apply_lanes(document, [{"name": "lane_a"}, {"name": "lane_b"}], document.stamp)

    assert _lane_a(result)["cost_per_m_tokens"] == 0.2
    assert _lane_a(result)["best_for"] == ["refactors"]


def test_unknown_lane_keys_are_preserved(tmp_path):
    path = tmp_path / "meister.config.yaml"
    path.write_bytes(CONFIG.replace("model: gpt-x\n", "model: gpt-x\n      custom_flag: true  # meu\n").encode("utf-8"))
    document = load_effective_lanes(str(path))

    apply_lanes(document, _request(max_retries=4), document.stamp)

    assert "custom_flag: true  # meu" in path.read_text(encoding="utf-8")


def test_lane_edit_carries_new_fields(tmp_path):
    document = _document(tmp_path)
    edit = LaneEdit(
        name="lane_a", harness="codex", model="gpt-x", cost_per_m_tokens=3, max_retries=1, best_for=["x"]
    )

    result = apply_lanes(document, [edit, {"name": "lane_b"}], document.stamp)

    assert _lane_a(result)["cost_per_m_tokens"] == 3
    assert _lane_a(result)["max_retries"] == 1
    assert "eligible_classes" not in _lane_a(result)


@pytest.mark.parametrize(
    "field_name, value",
    [
        ("cost_per_m_tokens", -1),
        ("cost_per_m_tokens", "2"),
        ("cost_per_m_tokens", True),
        ("cost_per_m_tokens", float("inf")),
        ("credit_usd", -0.5),
        ("credit_usd", "10"),
        ("credit_usd", False),
        ("max_retries", -1),
        ("max_retries", 1.5),
        ("max_retries", True),
        ("max_retries", "2"),
        ("idle_timeout_seconds", 0),
        ("idle_timeout_seconds", -3),
        ("idle_timeout_seconds", "60"),
        ("idle_timeout_seconds", True),
        ("max_runtime_seconds", 0),
        ("max_runtime_seconds", "60"),
        ("max_runtime_seconds", True),
        ("eligible_classes", "SMALL"),
        ("eligible_classes", ["TINY"]),
        ("eligible_classes", [1]),
        ("best_for", "texto"),
        ("best_for", ["ok", ""]),
        ("best_for", ["  "]),
        ("best_for", [3]),
    ],
)
def test_invalid_new_field_is_error_with_lane_path(tmp_path, field_name, value):
    document = _document(tmp_path)

    assert _paths(document, _request(**{field_name: value})) == [f"lanes[0].{field_name}"]


@pytest.mark.parametrize(
    "field_name, value",
    [
        ("credit_usd", None),
        ("cost_per_m_tokens", 0),
        ("max_retries", 0),
        ("idle_timeout_seconds", None),
        ("idle_timeout_seconds", 0.5),
        ("max_runtime_seconds", None),
        ("eligible_classes", ["small", "HIGH"]),
    ],
)
def test_boundary_values_are_valid(tmp_path, field_name, value):
    document = _document(tmp_path)

    assert _paths(document, _request(**{field_name: value})) == []
