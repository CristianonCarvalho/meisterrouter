import hashlib

from meister.config import _default_config_data
import pytest

from meister.config_editor import LaneDocument, LaneEdit, load_effective_lanes, validate_lanes
from meister.i18n import EN_MESSAGES, PT_BR_MESSAGES, t


PROJECT_CONFIG = """\
# Comentario do topo
workers:
  # vias do projeto
  tier_order:
    - name: lane_a  # primeira
      harness: codex
      model: gpt-x
      effort: medium
      cost_per_m_tokens: 0.2
    - name: lane_b
      harness: claude
      model: sonnet
      enabled: false
      max_parallel: 2
# fim
"""


def _default_lanes() -> list:
    return _default_config_data()["workers"]["tier_order"]


def test_reading_does_not_change_file_with_comments(tmp_path):
    path = tmp_path / "meister.config.yaml"
    path.write_text(PROJECT_CONFIG, encoding="utf-8")

    load_effective_lanes(str(path))

    assert path.read_text(encoding="utf-8") == PROJECT_CONFIG


def test_without_tier_order_returns_default_lanes_including_disabled(tmp_path):
    path = tmp_path / "meister.config.yaml"
    path.write_text("# so comentario\nconcurrency:\n  max_parallel_workers: 2\n", encoding="utf-8")

    document = load_effective_lanes(str(path))

    assert isinstance(document, LaneDocument)
    assert document.has_tier_order is False
    assert document.path == str(path)
    assert document.lanes == _default_lanes()
    assert any(lane.get("enabled") is False for lane in document.lanes) == any(
        lane.get("enabled") is False for lane in _default_lanes()
    )


def test_missing_file_returns_default_lanes_and_empty_stamp(tmp_path):
    path = tmp_path / "meister.config.yaml"

    document = load_effective_lanes(str(path))

    assert document.has_tier_order is False
    assert document.lanes == _default_lanes()
    assert document.stamp == hashlib.sha256(b"").hexdigest()
    assert not path.exists()


def test_with_tier_order_returns_project_lanes(tmp_path):
    path = tmp_path / "meister.config.yaml"
    path.write_text(PROJECT_CONFIG, encoding="utf-8")

    document = load_effective_lanes(str(path))

    assert document.has_tier_order is True
    assert [lane["name"] for lane in document.lanes] == ["lane_a", "lane_b"]
    assert document.lanes[0] == {
        "name": "lane_a",
        "harness": "codex",
        "model": "gpt-x",
        "effort": "medium",
        "cost_per_m_tokens": 0.2,
    }
    assert type(document.lanes[0]) is dict
    assert document.lanes[1]["enabled"] is False
    assert document.lanes[1]["max_parallel"] == 2


def test_stamp_changes_when_file_changes(tmp_path):
    path = tmp_path / "meister.config.yaml"
    path.write_text(PROJECT_CONFIG, encoding="utf-8")
    first = load_effective_lanes(str(path)).stamp

    assert load_effective_lanes(str(path)).stamp == first
    assert first == hashlib.sha256(PROJECT_CONFIG.encode("utf-8")).hexdigest()

    path.write_text(PROJECT_CONFIG.replace("gpt-x", "gpt-y"), encoding="utf-8")

    assert load_effective_lanes(str(path)).stamp != first


def test_lane_edit_holds_editable_fields():
    edit = LaneEdit(name="lane_a", harness="codex", model="gpt-x", effort=None, enabled=True, max_parallel=None)

    assert edit.name == "lane_a"
    assert edit.enabled is True


def _project_document(tmp_path) -> LaneDocument:
    path = tmp_path / "meister.config.yaml"
    path.write_text(PROJECT_CONFIG, encoding="utf-8")
    return load_effective_lanes(str(path))


def _valid_request() -> list:
    return [
        {"name": "lane_b", "harness": "claude", "model": "sonnet", "effort": "high", "enabled": True, "max_parallel": 3},
        {"name": "lane_a", "harness": "codex", "model": "gpt-x", "effort": "medium", "enabled": True, "max_parallel": None},
    ]


def _errors(issues) -> list:
    return [issue for issue in issues if issue.level == "error"]


def test_valid_request_has_no_errors_and_writes_nothing(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    document = _project_document(project)

    issues = validate_lanes(document, _valid_request())

    assert _errors(issues) == []
    assert (project / "meister.config.yaml").read_text(encoding="utf-8") == PROJECT_CONFIG
    assert sorted(path.name for path in project.iterdir()) == ["meister.config.yaml"]
    assert [lane["name"] for lane in document.lanes] == ["lane_a", "lane_b"]


def test_valid_request_accepts_lane_edit_objects(tmp_path):
    document = _project_document(tmp_path)
    lanes = [LaneEdit(**lane) for lane in _valid_request()]

    assert _errors(validate_lanes(document, lanes)) == []


def test_valid_request_against_default_lanes_without_file(tmp_path):
    document = load_effective_lanes(str(tmp_path / "meister.config.yaml"))
    lanes = [
        {key: lane.get(key) for key in ("name", "harness", "model", "effort", "enabled", "max_parallel")}
        for lane in document.lanes
    ]
    for lane in lanes:
        lane["enabled"] = lane["enabled"] is not False

    assert _errors(validate_lanes(document, lanes)) == []


def test_fields_may_be_omitted_and_keep_current_values(tmp_path):
    document = _project_document(tmp_path)

    issues = validate_lanes(document, [{"name": "lane_a"}, {"name": "lane_b"}])

    assert _errors(issues) == []


def test_no_enabled_lane_is_error(tmp_path):
    document = _project_document(tmp_path)
    lanes = _valid_request()
    for lane in lanes:
        lane["enabled"] = False

    issues = _errors(validate_lanes(document, lanes))

    assert [issue.path for issue in issues] == ["lanes"]
    assert issues[0].message == t("config_editor.no_enabled_lane")


def test_duplicate_name_is_error(tmp_path):
    document = _project_document(tmp_path)
    lanes = _valid_request()
    lanes[1]["name"] = "lane_b"

    paths = [issue.path for issue in _errors(validate_lanes(document, lanes))]

    assert "lanes[1].name" in paths


def test_unknown_lane_cannot_be_created(tmp_path):
    document = _project_document(tmp_path)
    lanes = _valid_request() + [{"name": "lane_new", "harness": "codex", "model": "m", "enabled": True}]

    paths = [issue.path for issue in _errors(validate_lanes(document, lanes))]

    assert paths == ["lanes[2].name"]


def test_missing_lane_cannot_be_removed(tmp_path):
    document = _project_document(tmp_path)

    issues = _errors(validate_lanes(document, _valid_request()[:1]))

    assert [issue.path for issue in issues] == ["lanes"]
    assert "lane_a" in issues[0].message


def test_missing_name_is_error(tmp_path):
    document = _project_document(tmp_path)
    lanes = _valid_request()
    del lanes[0]["name"]

    paths = [issue.path for issue in _errors(validate_lanes(document, lanes))]

    assert "lanes[0].name" in paths


def test_invalid_harness_is_error(tmp_path):
    document = _project_document(tmp_path)
    lanes = _valid_request()
    lanes[0]["harness"] = "bogus"

    paths = [issue.path for issue in _errors(validate_lanes(document, lanes))]

    assert paths == ["lanes[0].harness"]


def test_invalid_effort_for_harness_is_error(tmp_path):
    document = _project_document(tmp_path)
    lanes = _valid_request()
    lanes[1]["effort"] = "max"  # codex nao aceita max

    paths = [issue.path for issue in _errors(validate_lanes(document, lanes))]

    assert paths == ["lanes[1].effort"]


def test_null_effort_is_valid(tmp_path):
    document = _project_document(tmp_path)
    lanes = _valid_request()
    lanes[1]["effort"] = None

    assert _errors(validate_lanes(document, lanes)) == []


@pytest.mark.parametrize("model", ["", "   ", None, 3])
def test_empty_model_is_error(tmp_path, model):
    document = _project_document(tmp_path)
    lanes = _valid_request()
    lanes[0]["model"] = model

    paths = [issue.path for issue in _errors(validate_lanes(document, lanes))]

    assert paths == ["lanes[0].model"]


@pytest.mark.parametrize("value", [0, -1, 1.5, "2", True])
def test_invalid_max_parallel_is_error(tmp_path, value):
    document = _project_document(tmp_path)
    lanes = _valid_request()
    lanes[0]["max_parallel"] = value

    paths = [issue.path for issue in _errors(validate_lanes(document, lanes))]

    assert paths == ["lanes[0].max_parallel"]


def test_non_boolean_enabled_is_error(tmp_path):
    document = _project_document(tmp_path)
    lanes = _valid_request()
    lanes[0]["enabled"] = "yes"

    paths = [issue.path for issue in _errors(validate_lanes(document, lanes))]

    assert "lanes[0].enabled" in paths


@pytest.mark.parametrize("field_name", ["cost_per_m_tokens", "best_for", "credit_usd", "whatever"])
def test_non_editable_field_is_error(tmp_path, field_name):
    document = _project_document(tmp_path)
    lanes = _valid_request()
    lanes[0][field_name] = 1

    issues = _errors(validate_lanes(document, lanes))

    assert [issue.path for issue in issues] == [f"lanes[0].{field_name}"]


def test_lanes_must_be_a_list_of_objects(tmp_path):
    document = _project_document(tmp_path)

    assert [issue.path for issue in _errors(validate_lanes(document, {"name": "lane_a"}))] == ["lanes"]
    assert [issue.path for issue in _errors(validate_lanes(document, ["lane_a", _valid_request()[1]]))] == [
        "lanes[0]"
    ]


def test_config_validation_runs_on_resulting_document(tmp_path):
    path = tmp_path / "meister.config.yaml"
    path.write_text(PROJECT_CONFIG.replace("cost_per_m_tokens: 0.2", "credit_usd: -5"), encoding="utf-8")
    document = load_effective_lanes(str(path))

    paths = [issue.path for issue in _errors(validate_lanes(document, _valid_request()))]

    assert any(path.startswith("workers.tier_order") and path.endswith("credit_usd") for path in paths)


def test_config_editor_messages_exist_in_both_catalogs():
    keys = {key for key in EN_MESSAGES if key.startswith("config_editor.")}

    assert keys
    assert keys == {key for key in PT_BR_MESSAGES if key.startswith("config_editor.")}
    assert all(value.isascii() for key, value in EN_MESSAGES.items() if key in keys)
