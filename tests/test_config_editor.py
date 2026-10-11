import hashlib

from meister.config import _default_config_data
from meister.config_editor import LaneDocument, LaneEdit, load_effective_lanes


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
