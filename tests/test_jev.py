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
from meister.config import WorkerTier


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


def test_call_decisions_uses_configured_master_model(monkeypatch):
    from meister.config import MasterConfig, MeisterConfig

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        "meister.jev.load_config",
        lambda: MeisterConfig(master=MasterConfig(model="configured-decision-model")),
    )
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"answers": {"decision": {"choice": "ok"}}}
    with patch("requests.post", return_value=response) as post:
        call_decisions(state={}, questions={}, use_cache=False)
    assert post.call_args.kwargs["json"]["model"] == "configured-decision-model"


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
            "recommended_implementer": {"choice": "agy_gemini_flash", "confidence": 0.9},
        },
        "usage": {"input_tokens": 12, "output_tokens": 5, "cost": 0.00012},
    }

    with patch("meister.jev.call_decisions", return_value=mock_raw):
        res = classify_task("Refactor authentication module to support OAuth2")
        assert res["classification"] == "HIGH"
        assert res["recommended_implementer"] == "agy_gemini_flash"
        assert res["fallback_chain"] == ["claude_sonnet"]
        assert res["cost"] == 0.00012
        assert res["fallback_rule_applied"] is False


def test_classify_task_default_options_and_failure_fallback_use_configured_routes():
    configured_names = ["copilot_luna", "codex_luna", "agy_gemini_flash", "claude_sonnet"]
    response = {
        "answers": {
            "complexity": {"choice": "high"},
            "recommended_implementer": {"choice": "agy_gemini_flash"},
        },
        "usage": {},
    }
    with patch("meister.jev.call_decisions", return_value=response) as call:
        result = classify_task("A complex task")
    assert list(call.call_args.kwargs["questions"]["recommended_implementer"]["criteria"]) == configured_names
    assert result["recommended_implementer"] == "agy_gemini_flash"

    with patch("meister.jev.call_decisions", side_effect=RuntimeError("offline")):
        fallback = classify_task("A critical authentication migration")
    assert fallback["recommended_implementer"] == configured_names[0]
    assert fallback["fallback_chain"] == configured_names[1:]


def test_classify_task_deterministic_task_id():
    mock_raw = {
        "answers": {
            "complexity": {"choice": "medium", "confidence": 0.9},
            "recommended_implementer": {"choice": "copilot_luna", "confidence": 0.9},
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
        # Keyword fallback always chooses the first configured route.
        res_sec = classify_task("Fix critical authentication security vulnerability in OAuth")
        assert res_sec["fallback_rule_applied"] is True
        assert res_sec["classification"] == "HIGH"
        assert res_sec["recommended_implementer"] == "copilot_luna"
        assert res_sec["classification_confidence"] is None
        assert res_sec["implementer_confidence"] is None

        # 2. Regra para typo/doc -> SMALL / primeira via
        res_typo = classify_task("Fix typo in README documentation")
        assert res_typo["fallback_rule_applied"] is True
        assert res_typo["classification"] == "SMALL"
        assert res_typo["recommended_implementer"] == "copilot_luna"

        # 3. Regra genérica -> MEDIUM / primeira via
        res_gen = classify_task("Add user preference option in settings panel")
        assert res_gen["fallback_rule_applied"] is True
        assert res_gen["classification"] == "MEDIUM"
        assert res_gen["recommended_implementer"] == "copilot_luna"


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
        assert res["recommended_implementer"] == "copilot_luna"


def test_classify_task_old_environment_switch_has_no_effect(monkeypatch):
    monkeypatch.setenv("MEISTER_DISABLE_LUNA", "true")
    mock_raw = {
        "answers": {
            "complexity": {"choice": "small", "confidence": 0.99},
            "recommended_implementer": {"choice": "codex_luna", "confidence": 0.95},
        },
        "usage": {},
    }

    with patch("meister.jev.call_decisions", return_value=mock_raw):
        res = classify_task("Fix typo in readme")
        assert res["recommended_implementer"] == "codex_luna"


def test_classify_task_configured_implementers_build_criteria_and_order():
    implementers = [
        WorkerTier(
            name="copilot",
            harness="copilot",
            model="gpt-6-luna",
            cost_per_m_tokens=0.2,
            best_for=["github_integration", "code_completion"],
        ),
        WorkerTier(name="local", harness="codex", model="", best_for=["small_edits"]),
        WorkerTier(name="premium", harness="claude", model="sonnet", cost_per_m_tokens=0.0),
    ]
    mock_raw = {
        "answers": {
            "complexity": {"choice": "medium", "confidence": 0.9},
            "recommended_implementer": {"choice": "local", "confidence": 0.95},
        },
        "usage": {},
    }

    with patch("meister.jev.call_decisions", return_value=mock_raw) as mock_call:
        result = classify_task("Configured routes", implementers=implementers)

    criteria = mock_call.call_args.kwargs["questions"]["recommended_implementer"]["criteria"]
    assert list(criteria) == ["copilot", "local", "premium"]
    assert criteria["copilot"] == "gpt-6-luna via copilot ($0.2/M): github_integration, code_completion"
    assert criteria["local"] == "codex via codex: small_edits"
    assert criteria["premium"] == "sonnet via claude"
    assert result["recommended_implementer"] == "local"
    assert result["fallback_chain"] == ["premium"]


def test_classify_task_configured_invalid_answer_falls_back_to_first():
    implementers = [
        WorkerTier(name="copilot", harness="copilot"),
        WorkerTier(name="luna", harness="codex"),
    ]
    mock_raw = {
        "answers": {
            "complexity": {"choice": "medium"},
            "recommended_implementer": {"choice": "not-configured"},
        },
        "usage": {},
    }

    with patch("meister.jev.call_decisions", return_value=mock_raw):
        result = classify_task("Configured routes", implementers=implementers)

    assert result["recommended_implementer"] == "copilot"
    assert result["fallback_rule_applied"] is True
    assert result["fallback_chain"] == ["luna"]


def test_classify_task_configured_api_failure_uses_first_and_ordered_fallback():
    implementers = [
        WorkerTier(name="copilot", harness="copilot"),
        WorkerTier(name="luna", harness="codex"),
    ]
    with patch("meister.jev.call_decisions", side_effect=RuntimeError("offline")):
        result = classify_task("Security migration", implementers=implementers)

    assert result["recommended_implementer"] == "copilot"
    assert result["fallback_rule_applied"] is True
    assert result["fallback_chain"] == ["luna"]


def test_classify_task_configured_last_tier_has_empty_fallback_chain(monkeypatch):
    monkeypatch.setenv("MEISTER_DISABLE_LUNA", "true")
    monkeypatch.setenv("MEISTER_PRIMARY_WORKER", "unknown")
    implementers = [
        WorkerTier(name="copilot", harness="copilot"),
        WorkerTier(name="custom", harness="codex"),
    ]
    mock_raw = {
        "answers": {
            "complexity": {"choice": "small"},
            "recommended_implementer": {"choice": "custom"},
        },
        "usage": {},
    }

    with patch("meister.jev.call_decisions", return_value=mock_raw):
        result = classify_task("Fix typo", implementers=implementers)

    assert result["recommended_implementer"] == "custom"
    assert result["fallback_chain"] == []


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
        assert res_pass["action_confidence"] is None
        assert res_pass["should_escalate"] is False

        # 2. Testes falhando na 1ª tentativa -> RETRY
        res_fail1 = control_cycle(diff_summary="err diff", test_result="fail", attempts=1)
        assert res_fail1["fallback_rule_applied"] is True
        assert res_fail1["action"] == "RETRY"
        assert res_fail1["action_confidence"] is None
        assert res_fail1["should_escalate"] is False

        # 3. Testes falhando na 2ª tentativa -> ESCALATE
        res_fail2 = control_cycle(diff_summary="err diff", test_result="fail", attempts=2)
        assert res_fail2["fallback_rule_applied"] is True
        assert res_fail2["action"] == "ESCALATE"
        assert res_fail2["action_confidence"] is None
        assert res_fail2["should_escalate"] is True
        assert res_fail2["switch_implementer"] is True


def test_e2e10_fallback_rule_applied_null_confidence():
    """E2E-10: Fallback por regras do Jev reporta confiança nula (None / null em JSON) em vez de inventada."""
    import json

    with patch("meister.jev.call_decisions", side_effect=RuntimeError("Decisions API unavailable")):
        cls_res = classify_task("Some random code task")
        assert cls_res["fallback_rule_applied"] is True
        assert cls_res["classification_confidence"] is None
        assert cls_res["implementer_confidence"] is None

        cls_json = json.loads(json.dumps(cls_res))
        assert cls_json["classification_confidence"] is None
        assert cls_json["implementer_confidence"] is None

        ctl_res = control_cycle(diff_summary="diff", test_result="pass")
        assert ctl_res["fallback_rule_applied"] is True
        assert ctl_res["action_confidence"] is None

        ctl_json = json.loads(json.dumps(ctl_res))
        assert ctl_json["action_confidence"] is None
