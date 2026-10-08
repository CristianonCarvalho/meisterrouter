import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from meister.cli import main


def test_config_show_displays_lane_effort_in_text_and_json(tmp_path):
    config_file = tmp_path / "effort.yaml"
    config_file.write_text(
        """
workers:
  tier_order:
    - name: lane-with-effort
      harness: copilot
      model: test-model
      effort: high
    - name: lane-without-effort
      harness: copilot
      model: test-model
    - name: disabled-lane-with-effort
      harness: copilot
      model: test-model
      effort: medium
      enabled: false
"""
    )
    runner = CliRunner()

    text_result = runner.invoke(
        main, ["config", "show", "--config-path", str(config_file)]
    )
    json_result = runner.invoke(
        main, ["config", "show", "--json", "--config-path", str(config_file)]
    )

    assert text_result.exit_code == 0
    output_lines = text_result.output.splitlines()
    lines_by_lane = {
        name: next(line.split() for line in output_lines if name in line)
        for name in (
            "lane-with-effort",
            "lane-without-effort",
            "disabled-lane-with-effort",
        )
    }
    assert lines_by_lane["lane-with-effort"][4] == "high"
    assert lines_by_lane["lane-without-effort"][4] == "-"
    assert lines_by_lane["disabled-lane-with-effort"][3] == "medium"

    assert json_result.exit_code == 0
    workers = json.loads(json_result.output)["workers"]
    tiers = workers["tier_order"]
    assert [tier["effort"] for tier in tiers] == ["high", None]
    assert workers["disabled"][0]["effort"] == "medium"


def test_default_catalog_has_no_worker_effort_values():
    default_config = (
        Path(__file__).parents[1] / "meister" / "default_config.yaml"
    )

    with default_config.open(encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)

    assert all("effort" not in tier for tier in config["workers"]["tier_order"])
