from unittest.mock import MagicMock, patch
import pytest
import requests

from meister.jev import (
    call_decisions,
    classify_task,
    control_cycle,
    clear_decisions_cache,
    get_decisions_cache,
)


@pytest.fixture(autouse=True)
def clean_cache():
    """Ensure clean decisions cache before each test."""
    clear_decisions_cache()
    yield
    clear_decisions_cache()


# ─── Testes do call_decisions (Schema, Retry, Cache, Temperature) ────────────

def test_call_decisions_success_and_temperature(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "answers": {
            "complexity": {"choice": "medium", "confidence": 0.9},
        },
        "usage": {"input_tokens": 20, "output_tokens": 10, "cost": 0.00015},
    }

    with patch("requests.post", return_value=mock_resp) as mock_post:
        data = call_decisions(state={"task": "abc"}, questions={"q": {}}, use_cache=False)
        assert "answers" in data
        assert mock_post.call_count == 1

        # Verifica envio de temperature: 0.0 (Achado #24)
        _, kwargs = mock_post.call_args
        payload = kwargs.get("json", {})
        assert payload.get("temperature") == 0.0
        assert payload.get("state") == {"task": "abc"}


def test_call_decisions_input_hash_cache(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "answers": {
            "complexity": {"choice": "small", "confidence": 0.95},
        },
    }

    with patch("requests.post", return_value=mock_resp) as mock_post:
        # 1ª chamada: vai para a rede
        res1 = call_decisions(state={"foo": "bar"}, questions={"q1": {}}, use_cache=True)
        assert mock_post.call_count == 1
        assert len(get_decisions_cache()) == 1

        # 2ª chamada idêntica: usa cache, zero requisições adicionais (Achado #28)
        res2 = call_decisions(state={"foo": "bar"}, questions={"q1": {}}, use_cache=True)
        assert mock_post.call_count == 1
        assert res1 == res2


def test_call_decisions_retries_on_5xx_and_succeeds(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    error_resp = MagicMock()
    error_resp.status_code = 502

    success_resp = MagicMock()
    success_resp.status_code = 200
    success_resp.json.return_value = {
        "answers": {
            "complexity": {"choice": "medium", "confidence": 0.8},
        },
    }

    # 1 falha 502, seguida de sucesso
    with patch("requests.post", side_effect=[error_resp, success_resp]) as mock_post, \
         patch("time.sleep") as mock_sleep:
        data = call_decisions(state={"s": 1}, questions={"q": 1}, max_retries=3, use_cache=False)
        assert "answers" in data
        assert mock_post.call_count == 2
        assert mock_sleep.call_count == 1


def test_call_decisions_exhausts_retries_and_raises(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    with patch("requests.post", side_effect=requests.exceptions.ConnectionError("Network down")) as mock_post, \
         patch("time.sleep"):
        with pytest.raises(RuntimeError) as exc_info:
            call_decisions(state={"s": 1}, questions={"q": 1}, max_retries=3, use_cache=False)
        assert "após 3 tentativas" in str(exc_info.value)
        assert mock_post.call_count == 3


def test_call_decisions_malformed_pydantic_validation(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    # Retorno sem answers ou com formato incompatível com schema
    mock_resp.json.return_value = {"answers": "not-a-dict"}

    with patch("requests.post", return_value=mock_resp), patch("time.sleep"):
        with pytest.raises(RuntimeError) as exc_info:
            call_decisions(state={"s": 1}, questions={"q": 1}, max_retries=1, use_cache=False)
        assert "Pydantic validation" in str(exc_info.value)


# ─── Testes do classify_task (Cost, Deterministic ID, Fallback) ──────────────

def test_classify_task_tier_alignment():
    mock_raw = {
        "answers": {
            "complexity": {"choice": "high", "confidence": 0.95},
            "recommended_implementer": {"choice": "gemini_flash", "confidence": 0.9},
        },
        "usage": {"input_tokens": 12, "output_tokens": 5, "cost": 0.00012},
    }

    with patch("meister.jev.call_decisions", return_value=mock_raw):
        res = classify_task("Refactor authentication module to support OAuth2")
        assert res["classification"] == "HIGH"
        assert res["recommended_implementer"] == "gemini_flash"
        assert res["fallback_chain"] == ["haiku", "sonnet"]
        assert res["cost"] == 0.00012
        assert res["fallback_rule_applied"] is False


def test_classify_task_deterministic_task_id():
    mock_raw = {
        "answers": {
            "complexity": {"choice": "medium", "confidence": 0.9},
            "recommended_implementer": {"choice": "luna", "confidence": 0.9},
        },
        "usage": {},
    }

    with patch("meister.jev.call_decisions", return_value=mock_raw):
        res1 = classify_task("Identical prompt context")
        res2 = classify_task("Identical prompt context")
        # Achado #28: Task ID determinístico para chamadas idênticas
        assert res1["task_id"] == res2["task_id"]
        assert len(res1["task_id"]) == 16


def test_classify_task_rule_based_fallback_on_api_error():
    # Simula indisponibilidade total da OpenRouter
    with patch("meister.jev.call_decisions", side_effect=RuntimeError("OpenRouter 503 Service Unavailable")):
        # 1. Regra para segurança/auth -> HIGH / gemini_flash
        res_sec = classify_task("Fix critical authentication security vulnerability in OAuth")
        assert res_sec["fallback_rule_applied"] is True
        assert res_sec["classification"] == "HIGH"
        assert res_sec["recommended_implementer"] == "gemini_flash"

        # 2. Regra para typo/doc -> SMALL / luna
        res_typo = classify_task("Fix typo in README documentation")
        assert res_typo["fallback_rule_applied"] is True
        assert res_typo["classification"] == "SMALL"
        assert res_typo["recommended_implementer"] == "luna"

        # 3. Regra genérica -> MEDIUM / luna
        res_gen = classify_task("Add user preference option in settings panel")
        assert res_gen["fallback_rule_applied"] is True
        assert res_gen["classification"] == "MEDIUM"
        assert res_gen["recommended_implementer"] == "luna"


def test_classify_task_normalizes_unknown_choices():
    mock_raw = {
        "answers": {
            "complexity": {"choice": "super_extreme_unknown", "confidence": 0.5},
            "recommended_implementer": {"choice": "mysterious_model", "confidence": 0.5},
        },
        "usage": {},
    }

    with patch("meister.jev.call_decisions", return_value=mock_raw):
        res = classify_task("Some normal task")
        assert res["classification"] == "MEDIUM"
        assert res["recommended_implementer"] == "gemini_flash"


def test_classify_task_disable_luna(monkeypatch):
    monkeypatch.setenv("MEISTER_DISABLE_LUNA", "true")
    mock_raw = {
        "answers": {
            "complexity": {"choice": "small", "confidence": 0.99},
            "recommended_implementer": {"choice": "luna", "confidence": 0.95},
        },
        "usage": {},
    }

    with patch("meister.jev.call_decisions", return_value=mock_raw):
        res = classify_task("Fix typo in readme")
        assert res["recommended_implementer"] == "gemini_flash"


# ─── Testes do control_cycle (Gate Hard, Cost, Rule Fallback) ────────────────

def test_control_cycle_success_with_cost():
    mock_raw = {
        "answers": {
            "next_action": {"choice": "complete", "confidence": 0.98},
            "should_escalate": {"noul": 0.05},
            "switch_implementer": {"noul": 0.02},
        },
        "usage": {"prompt_tokens": 15, "completion_tokens": 4, "cost": 0.00008},
    }

    with patch("meister.jev.call_decisions", return_value=mock_raw):
        res = control_cycle(diff_summary="app.py +10", test_result="pass", attempts=1)
        assert res["action"] == "COMPLETE"
        assert res["should_escalate"] is False
        assert res["switch_implementer"] is False
        assert res["cost"] == 0.00008
        assert res["fallback_rule_applied"] is False


def test_control_cycle_hard_deterministic_gate_override():
    """Achado #4: Jev retornando COMPLETE com testes falhando DEVE ser forçado para RETRY."""
    mock_raw = {
        "answers": {
            "next_action": {"choice": "complete", "confidence": 0.99},
            "should_escalate": {"noul": 0.0},
            "switch_implementer": {"noul": 0.0},
        },
        "usage": {},
    }

    with patch("meister.jev.call_decisions", return_value=mock_raw):
        res = control_cycle(diff_summary="broken change", test_result="fail", attempts=1)
        assert res["action"] == "RETRY"


def test_control_cycle_rule_based_fallback_on_api_error():
    with patch("meister.jev.call_decisions", side_effect=RuntimeError("Decisions API down")):
        # 1. Testes passando -> COMPLETE
        res_pass = control_cycle(diff_summary="clean diff", test_result="pass", attempts=1)
        assert res_pass["fallback_rule_applied"] is True
        assert res_pass["action"] == "COMPLETE"
        assert res_pass["should_escalate"] is False

        # 2. Testes falhando na 1ª tentativa -> RETRY
        res_fail1 = control_cycle(diff_summary="err diff", test_result="fail", attempts=1)
        assert res_fail1["fallback_rule_applied"] is True
        assert res_fail1["action"] == "RETRY"
        assert res_fail1["should_escalate"] is False

        # 3. Testes falhando na 2ª tentativa -> ESCALATE
        res_fail2 = control_cycle(diff_summary="err diff", test_result="fail", attempts=2)
        assert res_fail2["fallback_rule_applied"] is True
        assert res_fail2["action"] == "ESCALATE"
        assert res_fail2["should_escalate"] is True
        assert res_fail2["switch_implementer"] is True
