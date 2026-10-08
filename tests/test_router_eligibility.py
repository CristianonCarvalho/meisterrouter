import uuid
from unittest.mock import AsyncMock, patch

import pytest

from meister.config import load_config
from meister.herdr.bridge import HerdrEventBridge
from meister.state import StateManager
from meister.worker import write_atomic_json


def _make_router_bridge(tmp_path, tiers, mode="jev"):
    scenario_id = uuid.uuid4().hex
    config_path = tmp_path / f"router_eligibility_{scenario_id}.yaml"
    config_path.write_text(
        f"router:\n  mode: {mode}\n"
        "workers:\n  tier_order:\n"
        + "".join(
            f"    - name: {name}\n"
            "      harness: codex\n"
            f"      model: model-{name}\n"
            + (f"      eligible_classes: [{','.join(classes)}]\n" if classes else "")
            for name, classes in tiers
        )
        + "concurrency:\n  isolation_mode: none\n  layout_strategy: tiled\n"
    )
    client = AsyncMock()
    client.read_pane.return_value = "Done"
    config = load_config(str(config_path))
    bridge = HerdrEventBridge(
        config=config,
        client=client,
        state_manager=StateManager(str(tmp_path / f"state_{scenario_id}.db")),
    )
    spawned = []

    async def fake_spawn(tier_name, task_context=None, **kwargs):
        spawned.append(tier_name)
        if len(spawned) == 1 and task_context.get("return_quota"):
            result = {
                "status": "error",
                "output": "HTTP 429: Insufficient quota balance",
            }
        else:
            result = {"status": "done", "modified_files": []}
        write_atomic_json(task_context["result_file"], result)
        return f"pane-{len(spawned)}", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = fake_spawn
    subtask = {
        "id": "eligibility-task",
        "description": "Route according to class",
        "target_files": ["src/task.py"],
        "cwd": str(tmp_path),
    }
    return bridge, spawned, subtask


async def _execute(bridge, subtask, classification, recommendation, quota=False):
    result = {
        "classification": classification,
        "classification_confidence": 0.9,
        "recommended_implementer": recommendation,
        "fallback_rule_applied": False,
    }
    subtask = {**subtask, "return_quota": quota}
    with patch("meister.herdr.bridge.classify_task", return_value=result), patch(
        "meister.herdr.bridge.log_event"
    ) as log_event:
        success = await bridge.execute_subtask(subtask, run_id="eligibility-run")
    route_events = [
        call.kwargs
        for call in log_event.call_args_list
        if call.kwargs.get("event_type") == "route_decision"
    ]
    return success, route_events


@pytest.mark.asyncio
async def test_high_class_replaces_restricted_recommendation_with_nearest_prior_tier(tmp_path):
    bridge, spawned, subtask = _make_router_bridge(
        tmp_path,
        [
            ("tier_1b", []),
            ("tier_2", []),
            ("tier_3", ["ESCALATE"]),
            ("later_open_tier", []),
        ],
    )

    success, routes = await _execute(bridge, subtask, "HIGH", "tier_3")

    assert success
    assert spawned == ["tier_2"]
    assert routes[0]["tier"] == "tier_2"
    assert routes[0]["jev_recommended"] == "tier_3"
    assert routes[0]["status"] == "ineligible_replaced"
    assert routes[0]["fallback_rule_applied"] is True


@pytest.mark.asyncio
async def test_eligible_recommendation_and_unrestricted_route_keep_existing_event_shape(tmp_path):
    bridge, spawned, subtask = _make_router_bridge(
        tmp_path, [("open", []), ("restricted", ["ESCALATE"])]
    )
    success, routes = await _execute(bridge, subtask, "ESCALATE", "restricted")
    assert success
    assert spawned == ["restricted"]
    assert routes == [
        {
            "event_type": "route_decision",
            "run_id": "eligibility-run",
            "task_id": "eligibility-task",
            "subtask_id": routes[0]["subtask_id"],
            "tier": "restricted",
            "classification": "ESCALATE",
            "confidence": 0.9,
            "fallback_rule_applied": False,
        }
    ]

    bridge, spawned, subtask = _make_router_bridge(
        tmp_path, [("open", []), ("restricted", ["ESCALATE"])]
    )
    success, routes = await _execute(bridge, subtask, "HIGH", "open")
    assert success
    assert spawned == ["open"]
    assert set(routes[0]) == {
        "event_type",
        "run_id",
        "task_id",
        "subtask_id",
        "tier",
        "classification",
        "confidence",
        "fallback_rule_applied",
    }


@pytest.mark.asyncio
async def test_no_eligible_replacement_keeps_jev_recommendation(tmp_path):
    bridge, spawned, subtask = _make_router_bridge(
        tmp_path,
        [
            ("small_only", ["SMALL"]),
            ("escalate_only", ["ESCALATE"]),
        ],
    )

    success, routes = await _execute(bridge, subtask, "HIGH", "escalate_only")

    assert success
    assert spawned == ["escalate_only"]
    assert routes[0]["tier"] == "escalate_only"
    assert "status" not in routes[0]
    assert "jev_recommended" not in routes[0]


@pytest.mark.asyncio
async def test_replacement_uses_next_tier_when_no_prior_tier_is_eligible(tmp_path):
    bridge, spawned, subtask = _make_router_bridge(
        tmp_path,
        [
            ("small_only", ["SMALL"]),
            ("restricted", ["ESCALATE"]),
            ("open_after", []),
        ],
    )

    success, routes = await _execute(bridge, subtask, "HIGH", "restricted")

    assert success
    assert spawned == ["open_after"]
    assert routes[0]["status"] == "ineligible_replaced"


@pytest.mark.asyncio
async def test_lowercase_classification_matches_eligibility_case_insensitively(tmp_path):
    bridge, spawned, subtask = _make_router_bridge(
        tmp_path, [("open", []), ("restricted", ["HIGH"])]
    )

    success, routes = await _execute(bridge, subtask, "high", "restricted")

    assert success
    assert spawned == ["restricted"]
    assert "status" not in routes[0]


@pytest.mark.asyncio
async def test_first_router_mode_ignores_eligibility_and_does_not_call_jev(tmp_path):
    bridge, spawned, subtask = _make_router_bridge(
        tmp_path,
        [("restricted", ["ESCALATE"]), ("open", [])],
        mode="first",
    )
    with patch(
        "meister.herdr.bridge.classify_task",
        side_effect=AssertionError("Jev must not be called"),
    ):
        assert await bridge.execute_subtask(subtask) is True

    assert spawned == ["restricted"]


@pytest.mark.asyncio
async def test_quota_fallback_can_reach_tier_restricted_for_initial_routing(tmp_path):
    bridge, spawned, subtask = _make_router_bridge(
        tmp_path, [("open", []), ("restricted", ["ESCALATE"])]
    )

    success, _ = await _execute(
        bridge, subtask, "HIGH", "open", quota=True
    )

    assert success
    assert spawned == ["open", "restricted"]
