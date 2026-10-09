"""Contrato do corpo enviado ao Jev por classify e control.

Se um campo for acrescentado, renomeado ou tiver outro tipo, este teste quebra e obriga a
revisar docs/jev-data-sent.md e docs/pt-BR/DADOS_ENVIADOS_AO_JEV.md.
"""

import json
from typing import Any, Dict, List

import pytest

from meister import jev
from meister.config import WorkerTier

SENTINEL_KEY = "sk-or-contract-test-sentinel"
MODEL = "contract-test-model"
IMPLEMENTERS = [
    WorkerTier(name="contract-lane-one", harness="copilot", model="model-one", cost_per_m_tokens=0.2),
    WorkerTier(name="contract-lane-two", harness="agy", model="model-two", cost_per_m_tokens=1.5),
]
LANE_KEYS = ["lane_a", "lane_b"]


class _FakeResponse:
    status_code = 200

    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Dict[str, Any]:
        return self._data


@pytest.fixture
def captured(monkeypatch):
    """Simula requests.post e guarda cada chamada (url, headers, json)."""
    calls: List[Dict[str, Any]] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json})
        if "complexity" in json["questions"]:
            answers = {
                "complexity": {"choice": "small", "confidence": 0.9},
                "recommended_implementer": {"choice": LANE_KEYS[0], "confidence": 0.9},
            }
        else:
            answers = {
                "next_action": {"choice": "complete", "confidence": 0.9},
                "should_escalate": {"noul": 0.1},
                "switch_implementer": {"noul": 0.1},
            }
        return _FakeResponse({"answers": answers, "usage": {}})

    monkeypatch.setattr(jev.requests, "post", fake_post)
    monkeypatch.setattr(jev, "get_api_key", lambda: SENTINEL_KEY)
    return calls


def _assert_fields(obj: Dict[str, Any], expected: Dict[str, type]) -> None:
    assert set(obj) == set(expected), f"campos inesperados: {sorted(set(obj) ^ set(expected))}"
    for name, kind in expected.items():
        assert isinstance(obj[name], kind), f"{name} deveria ser {kind.__name__}"


def _assert_no_key_in_body(call: Dict[str, Any]) -> None:
    body = json.dumps(call["json"], sort_keys=True)
    assert SENTINEL_KEY not in body
    assert "Authorization" not in body
    assert call["headers"]["Authorization"].endswith(SENTINEL_KEY)


def test_classify_body_has_exact_fields(captured):
    jev.classify_task(
        "Ajustar texto do README",
        model=MODEL,
        implementers=IMPLEMENTERS,
        use_cache=False,
    )

    assert len(captured) == 1
    body = captured[0]["json"]
    _assert_fields(body, {"model": str, "temperature": float, "state": dict, "questions": dict})
    assert body["model"] == MODEL
    assert body["temperature"] == 0.0

    _assert_fields(body["state"], {"task_description": str})
    assert body["state"]["task_description"] == "Ajustar texto do README"

    questions = body["questions"]
    _assert_fields(questions, {"complexity": dict, "recommended_implementer": dict})

    complexity = questions["complexity"]
    _assert_fields(complexity, {"type": str, "instructions": str, "criteria": dict})
    assert complexity["type"] == "choice"
    assert set(complexity["criteria"]) == {"small", "medium", "high", "escalate"}

    implementer = questions["recommended_implementer"]
    _assert_fields(implementer, {"type": str, "instructions": str, "criteria": dict})
    assert implementer["type"] == "choice"
    assert set(implementer["criteria"]) == set(LANE_KEYS)
    assert all(isinstance(value, str) for value in implementer["criteria"].values())


def test_classify_sends_opaque_lane_keys_not_real_names(captured):
    jev.classify_task("Tarefa", model=MODEL, implementers=IMPLEMENTERS, use_cache=False)

    body_text = json.dumps(captured[0]["json"])
    for tier in IMPLEMENTERS:
        assert tier.name not in body_text


def test_classify_key_only_in_authorization_header(captured):
    jev.classify_task("Tarefa", model=MODEL, implementers=IMPLEMENTERS, use_cache=False)

    _assert_no_key_in_body(captured[0])


def test_control_body_has_exact_fields(captured):
    jev.control_cycle(
        diff_summary="M meister/jev.py\nA tests/test_x.py",
        test_result="pass",
        attempts=2,
        security_sensitive=True,
        model=MODEL,
        use_cache=False,
    )

    assert len(captured) == 1
    body = captured[0]["json"]
    _assert_fields(body, {"model": str, "temperature": float, "state": dict, "questions": dict})
    assert body["model"] == MODEL

    _assert_fields(
        body["state"],
        {"diff_summary": str, "test_result": str, "attempts_so_far": int, "security_sensitive": bool},
    )
    assert body["state"]["diff_summary"] == "M meister/jev.py\nA tests/test_x.py"

    questions = body["questions"]
    _assert_fields(questions, {"next_action": dict, "should_escalate": dict, "switch_implementer": dict})
    assert questions["next_action"]["type"] == "choice"
    assert set(questions["next_action"]["criteria"]) == {"continue", "retry", "verify", "escalate", "complete"}
    assert questions["should_escalate"]["type"] == "noul"
    assert questions["switch_implementer"]["type"] == "noul"


def test_control_key_only_in_authorization_header(captured):
    jev.control_cycle(
        diff_summary="M a.py",
        test_result="fail",
        model=MODEL,
        use_cache=False,
    )

    _assert_no_key_in_body(captured[0])
