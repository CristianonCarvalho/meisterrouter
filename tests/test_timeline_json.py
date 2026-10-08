from __future__ import annotations

import json

from click.testing import CliRunner

from meister.cli import main
from meister.timeline import build_timeline
from meister.timeline_graph import build_graph
from meister.timeline_json import timeline_to_dict
from tests.timeline_fixtures import at, parallel_events


def test_timeline_json_is_serializable_and_has_schema_and_graph_data(tmp_path):
    events = parallel_events()
    timeline = build_timeline(events, "r1", at(30))
    graph = build_graph(timeline, {"tier_1b": 0, "tier_2": 1}, at(30), events)

    data = timeline_to_dict(timeline, graph, project=str(tmp_path), now=at(30))

    assert json.loads(json.dumps(data))["schema"] == 1
    assert data["project"]["name"] == tmp_path.name
    assert data["run"]["id"] == "r1"
    assert data["rows"][0]["segments"]
    assert data["edges"]
    assert data["critical_path"] == graph.critical_path
    assert data["jev"]["calls"] == []
    running_row = next(row for row in data["rows"] if row["task_id"] == "task_3")
    assert any(
        segment["end"] == at(30).isoformat().replace("+00:00", "Z")
        for segment in running_row["segments"]
    )


def test_project_hue_is_stable_for_the_same_path(tmp_path):
    events = parallel_events()
    timeline = build_timeline(events, "r1", at(30))
    graph = build_graph(timeline, {}, at(30), events)

    first = timeline_to_dict(timeline, graph, project=str(tmp_path), now=at(30))
    second = timeline_to_dict(timeline, graph, project=str(tmp_path), now=at(30))

    assert first["project"]["hue"] == second["project"]["hue"]
    assert 0 <= first["project"]["hue"] <= 359


def test_timeline_json_without_log_returns_empty_skeleton_without_writing(tmp_path):
    log_dir = tmp_path / "no-log"
    log_dir.mkdir()
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    result = CliRunner().invoke(
        main, ["timeline", "--json", "--log-dir", str(log_dir)]
    )

    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["schema"] == 1
    assert data["run"] is None
    assert data["rows"] == []
    assert before == after
