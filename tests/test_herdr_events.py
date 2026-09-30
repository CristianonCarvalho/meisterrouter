import json
from pathlib import Path
import pytest

from meister.herdr.events import (
    PANE_GONE_TYPES,
    parse_pane_event,
    is_pane_gone,
)


@pytest.fixture
def real_events():
    fixture_path = Path(__file__).parent / "fixtures" / "herdr_events.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_pane_gone_types():
    assert PANE_GONE_TYPES == frozenset({"pane_exited", "pane_closed"})


@pytest.mark.parametrize(
    "event,expected_type,expected_pane_id",
    [
        # (i) Formato REAL do Herdr (capturado em 2026-09-30)
        (
            {"data": {"pane_id": "w9:pFM", "type": "pane_exited", "workspace_id": "w9"}, "event": "pane_exited"},
            "pane_exited",
            "w9:pFM",
        ),
        (
            {"data": {"pane_id": "w9:pFG", "type": "pane_closed", "workspace_id": "w9"}, "event": "pane_closed"},
            "pane_closed",
            "w9:pFG",
        ),
        # (ii) Formato plano
        (
            {"type": "pane_exited", "pane_id": "w1:p1"},
            "pane_exited",
            "w1:p1",
        ),
        (
            {"type": "pane_closed", "pane_id": "w1:p2"},
            "pane_closed",
            "w1:p2",
        ),
        # (iii) Formato RPC com method e params
        (
            {"method": "pane.exited", "params": {"pane_id": "w1:p3"}},
            "pane_exited",
            "w1:p3",
        ),
        (
            {"method": "pane.closed", "params": {"pane_id": "w1:p4"}},
            "pane_closed",
            "w1:p4",
        ),
        # (iv) Embrulhado em params
        (
            {
                "params": {
                    "event": "pane_exited",
                    "data": {"pane_id": "w9:p5", "type": "pane_exited"},
                }
            },
            "pane_exited",
            "w9:p5",
        ),
        (
            {
                "params": {
                    "type": "pane.exited",
                    "pane_id": "w1:p6",
                }
            },
            "pane_exited",
            "w1:p6",
        ),
        # Tipos com . e _
        (
            {"event": "pane.exited", "data": {"pane_id": "w1:p7"}},
            "pane_exited",
            "w1:p7",
        ),
        (
            {"type": "PANE.EXITED", "data": {"pane_id": "w1:p8"}},
            "pane_exited",
            "w1:p8",
        ),
        # Entradas inválidas
        (None, None, None),
        ("not a dict", None, None),
        ([], None, None),
        (123, None, None),
        ({}, None, None),
        ({"event": "pane_exited", "data": {"pane_id": ""}}, "pane_exited", None),
        ({"event": "pane_exited", "data": {"pane_id": 123}}, "pane_exited", None),
        ({"event": "pane_exited", "data": {}}, "pane_exited", None),
        ({"pane_id": "w1:p1"}, None, "w1:p1"),
    ],
)
def test_parse_pane_event_table(event, expected_type, expected_pane_id):
    ev_type, pane_id = parse_pane_event(event)
    assert ev_type == expected_type
    assert pane_id == expected_pane_id


def test_parse_pane_event_from_fixture(real_events):
    ev_type, pane_id = parse_pane_event(real_events["pane_exited"])
    assert ev_type == "pane_exited"
    assert pane_id == "w9:pFM"

    ev_type, pane_id = parse_pane_event(real_events["pane_closed"])
    assert ev_type == "pane_closed"
    assert pane_id == "w9:pFG"

    ev_type, pane_id = parse_pane_event(real_events["pane_created"])
    assert ev_type == "pane_created"
    # pane_created real tem pane_id aninhado em data.pane, mas parse_pane_event foca em data, params e nível superior
    # o spec diz: 'pane_id': procurar em data, params e no nível de cima (nessa ordem); só aceitar str não vazia


def test_is_pane_gone(real_events):
    # pane_exited real com pane_id correto
    assert is_pane_gone(real_events["pane_exited"], "w9:pFM") is True
    # pane_closed real com pane_id correto
    assert is_pane_gone(real_events["pane_closed"], "w9:pFG") is True

    # pane_id diferente
    assert is_pane_gone(real_events["pane_exited"], "other:pane") is False
    assert is_pane_gone(real_events["pane_closed"], "other:pane") is False

    # pane_created não é gone
    assert is_pane_gone(real_events["pane_created"], "w9:p1") is False

    # formato plano
    assert is_pane_gone({"type": "pane_exited", "pane_id": "w1:p1"}, "w1:p1") is True
    assert is_pane_gone({"type": "pane_closed", "pane_id": "w1:p1"}, "w1:p1") is True
    assert is_pane_gone({"type": "pane_created", "pane_id": "w1:p1"}, "w1:p1") is False
    assert is_pane_gone({"type": "pane_exited", "pane_id": "w1:p2"}, "w1:p1") is False

    # entrada inválida
    assert is_pane_gone(None, "w1:p1") is False
    assert is_pane_gone({}, "w1:p1") is False
