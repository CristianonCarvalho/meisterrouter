"""Herdr Event Normalizer and Utilities.

Formato REAL dos eventos medido no Herdr em 2026-09-30 (probe em ~/.meister/handoff/pane_events_probe.py):
- Linhas cruas do socket do Herdr para pane_closed e pane_exited contêm o payload:
    {"data": {"pane_id": "w9:pFG", "type": "pane_closed", "workspace_id": "w9"}, "event": "pane_closed"}
    {"data": {"pane_id": "w9:pFM", "type": "pane_exited", "workspace_id": "w9"}, "event": "pane_exited"}
  onde o pane_id real fica aninhado dentro de 'data'.
- pane.created também vem aninhado:
    {"data": {"pane": {..., "pane_id": "..."}, ...}, "event": "pane_created"}
"""

from typing import Any, Optional

PANE_GONE_TYPES = frozenset({"pane_exited", "pane_closed"})


def parse_pane_event(event: Any) -> tuple[Optional[str], Optional[str]]:
    """Parse and normalize Herdr pane events.

    Returns:
        (normalized_type, pane_id) where:
        - normalized_type is lowercase with '.' replaced by '_' (e.g. 'pane.exited' -> 'pane_exited')
        - pane_id is a non-empty string or None.
    """
    if not isinstance(event, dict):
        return None, None

    params = event.get("params")
    params_dict: dict[str, Any] = params if isinstance(params, dict) else {}
    raw_data = event.get("data")
    if not isinstance(raw_data, dict):
        nested_data = params_dict.get("data")
        raw_data = nested_data if isinstance(nested_data, dict) else {}
    data_dict: dict[str, Any] = raw_data

    # Prioridade para achar o tipo: event, type (no nível e em data/params), method.
    raw_type: Optional[str] = (
        event.get("event")
        or params_dict.get("event")
        or event.get("type")
        or data_dict.get("type")
        or params_dict.get("type")
        or event.get("method")
        or params_dict.get("method")
    )

    norm_type: Optional[str] = None
    if isinstance(raw_type, str) and raw_type.strip():
        norm_type = raw_type.strip().lower().replace(".", "_")

    # pane_id: procurar em data, params e no nível de cima (nessa ordem); só aceitar str não vazia
    raw_pane_id = data_dict.get("pane_id") or params_dict.get("pane_id") or event.get("pane_id")
    norm_pane_id: Optional[str] = None
    if isinstance(raw_pane_id, str) and raw_pane_id.strip():
        norm_pane_id = raw_pane_id.strip()

    return norm_type, norm_pane_id


def is_pane_gone(event: Any, pane_id: str) -> bool:
    """Check if event signals that the specified pane has exited or closed."""
    if not isinstance(event, dict) or not isinstance(pane_id, str) or not pane_id:
        return False
    ev_type, target_pane_id = parse_pane_event(event)
    return ev_type in PANE_GONE_TYPES and target_pane_id == pane_id
