import pytest

from meister.config_editor import (
    ConfigValidationError,
    LaneDocument,
    apply_lanes,
    load_effective_lanes,
    validate_lanes,
)
from meister.i18n import t


CONFIG = """\
workers:
  tier_order:
    - name: tier_1  # primeira
      harness: codex
      model: gpt-x
      cost_per_m_tokens: 0.2
    - name: tier_2
      harness: claude
      model: sonnet
      cost_per_m_tokens: 4.0
"""

NON_STANDARD_CONFIG = """\
workers:
  tier_order:
    - name: fast
      harness: codex
      model: gpt-x
      cost_per_m_tokens: 0.2
    - name: slow
      harness: claude
      model: sonnet
      cost_per_m_tokens: 4.0
"""


def _document(tmp_path, content: str = CONFIG) -> LaneDocument:
    path = tmp_path / "meister.config.yaml"
    path.write_bytes(content.encode("utf-8"))
    return load_effective_lanes(str(path))


def _request(document: LaneDocument) -> list:
    return [{"original_name": lane["name"], **lane} for lane in document.lanes]


def _new(name: str, **extra) -> dict:
    lane = {"name": name, "harness": "codex", "model": "gpt-y", "cost_per_m_tokens": 0.5}
    lane.update(extra)
    return lane


def _errors(issues) -> list:
    return [issue for issue in issues if issue.level == "error"]


def _paths(document, lanes) -> list:
    return [issue.path for issue in _errors(validate_lanes(document, lanes))]


def test_create_lane_in_the_middle(tmp_path):
    document = _document(tmp_path)
    lanes = _request(document)
    lanes.insert(1, _new("tier_1b"))

    result = apply_lanes(document, lanes, document.stamp)

    assert [lane["name"] for lane in result.lanes] == ["tier_1", "tier_1b", "tier_2"]
    assert result.lanes[1] == {"name": "tier_1b", "harness": "codex", "model": "gpt-y", "cost_per_m_tokens": 0.5}
    assert "# primeira" in (tmp_path / "meister.config.yaml").read_text(encoding="utf-8")


def test_create_lane_at_the_end_omits_null_keys(tmp_path):
    document = _document(tmp_path)
    lanes = _request(document) + [_new("tier_3", effort=None, max_parallel=None, credit_usd=None, best_for=None)]

    result = apply_lanes(document, lanes, document.stamp)

    assert [lane["name"] for lane in result.lanes] == ["tier_1", "tier_2", "tier_3"]
    assert set(result.lanes[2]) == {"name", "harness", "model", "cost_per_m_tokens"}


def test_remove_lane(tmp_path):
    document = _document(tmp_path)

    result = apply_lanes(document, _request(document)[1:], document.stamp)

    assert [lane["name"] for lane in result.lanes] == ["tier_2"]
    assert "# primeira" not in (tmp_path / "meister.config.yaml").read_text(encoding="utf-8")


def test_removing_every_enabled_lane_is_refused(tmp_path):
    document = _document(tmp_path)
    lanes = _request(document)
    for lane in lanes:
        lane["enabled"] = False

    issues = _errors(validate_lanes(document, lanes))

    assert [issue.message for issue in issues] == [t("config_editor.no_enabled_lane")]


def test_rename_keeps_node_comment(tmp_path):
    document = _document(tmp_path)
    lanes = _request(document)
    lanes[0]["name"] = "tier_1c"

    result = apply_lanes(document, lanes, document.stamp)

    text = (tmp_path / "meister.config.yaml").read_text(encoding="utf-8")
    assert [lane["name"] for lane in result.lanes] == ["tier_1c", "tier_2"]
    assert "name: tier_1c # primeira" in text
    assert "original_name" not in text
    assert result.lanes[0]["model"] == "gpt-x"


def test_rename_to_existing_name_is_refused(tmp_path):
    document = _document(tmp_path)
    lanes = _request(document)
    lanes[0]["name"] = "tier_2"

    assert _paths(document, lanes) == ["lanes[1].name"]


def test_new_lane_with_repeated_name_is_refused(tmp_path):
    document = _document(tmp_path)
    lanes = _request(document) + [_new("tier_3"), _new("tier_3")]

    assert _paths(document, lanes) == ["lanes[3].name"]


@pytest.mark.parametrize("name", ["fast", "tier_", "tier_1bb", "Tier_1", "1_tier1", "tier-1"])
def test_invalid_new_names_are_refused(tmp_path, name):
    document = _document(tmp_path)
    lanes = _request(document) + [_new(name)]

    assert _paths(document, lanes) == ["lanes[2].name"]


@pytest.mark.parametrize("name", ["tier_3", "tier_3b", "gpt_3"])
def test_valid_new_names_are_accepted(tmp_path, name):
    document = _document(tmp_path)

    assert _errors(validate_lanes(document, _request(document) + [_new(name)])) == []


def test_invalid_rename_is_refused(tmp_path):
    document = _document(tmp_path)
    lanes = _request(document)
    lanes[1]["name"] = "slow"

    assert _paths(document, lanes) == ["lanes[1].name"]


def test_decreasing_level_is_refused(tmp_path):
    document = _document(tmp_path)
    lanes = _request(document) + [_new("tier_1b")]

    assert _paths(document, lanes) == ["lanes[2].name"]


def test_same_level_with_letter_is_accepted(tmp_path):
    document = _document(tmp_path)
    lanes = _request(document)
    lanes.insert(1, _new("tier_1c"))

    assert _errors(validate_lanes(document, lanes)) == []


def test_new_lane_without_cost_is_refused(tmp_path):
    document = _document(tmp_path)
    lane = _new("tier_3")
    del lane["cost_per_m_tokens"]

    assert _paths(document, _request(document) + [lane]) == ["lanes[2].cost_per_m_tokens"]


def test_new_lane_reports_every_missing_required_field(tmp_path):
    document = _document(tmp_path)

    assert _paths(document, _request(document) + [{"name": "tier_3"}]) == [
        "lanes[2].harness",
        "lanes[2].model",
        "lanes[2].cost_per_m_tokens",
    ]


def test_non_standard_names_are_kept_when_not_renamed(tmp_path):
    document = _document(tmp_path, NON_STANDARD_CONFIG)
    lanes = _request(document)
    lanes.reverse()
    lanes[0]["model"] = "outro"

    result = apply_lanes(document, lanes, document.stamp)

    assert [lane["name"] for lane in result.lanes] == ["slow", "fast"]
    assert result.lanes[0]["model"] == "outro"


def test_request_without_original_name_updates_existing_lane(tmp_path):
    document = _document(tmp_path)
    lanes = [{"name": "tier_1", "model": "novo"}, {"name": "tier_2"}]

    result = apply_lanes(document, lanes, document.stamp)

    assert [lane["name"] for lane in result.lanes] == ["tier_1", "tier_2"]
    assert result.lanes[0]["model"] == "novo"


def test_invalid_request_raises_validation_error_and_writes_nothing(tmp_path):
    document = _document(tmp_path)

    with pytest.raises(ConfigValidationError):
        apply_lanes(document, _request(document) + [_new("fast")], document.stamp)

    assert (tmp_path / "meister.config.yaml").read_text(encoding="utf-8") == CONFIG
