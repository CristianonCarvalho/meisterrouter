import os
import time
import pytest
from unittest.mock import AsyncMock, patch

from meister.config import MeisterConfig, WorkersConfig, WorkerTier, ConcurrencyConfig
from meister.herdr.bridge import HerdrEventBridge
from meister.herdr.workers import WorkerSpawner, detect_quota_or_rate_limit
from meister.state import StateManager


@pytest.fixture
def state_mgr(tmp_path):
    db_path = str(tmp_path / "test_cb.db")
    mgr = StateManager(db_path=db_path)
    try:
        yield mgr
    finally:
        mgr.close()


# =============================================================================
# 1. Refined Quota Detection & False Positive Immunity (Achado #22)
# =============================================================================


def test_detect_quota_false_positives_eliminated():
    """Verifica que o detector não dispara falsos positivos em linhas de código, testes ou middlewares (Achado #22)."""
    # Casos exatos documentados no Achado #22:
    false_positives = [
        "rate limit middleware",
        "status: 404 in test",
        "handler for HTTP 429",
        "model not found error handling",
        "def test_rate_limit_exceeded():",
        "assert error.status_code == 429",
        "class RateLimitHandler(BaseMiddleware):",
        "// We handle rate limit error gracefully",
        "/* In test environment, status 429 is mocked */",
    ]
    for text in false_positives:
        assert not detect_quota_or_rate_limit(text), f"Falso positivo incorretamente detectado para: '{text}'"


def test_detect_quota_structured_json():
    """Verifica que sinais estruturados JSON (CLI / OpenAI / Anthropic / Google) são detectados com precisão."""
    # Sinais estruturados com type: rate_limits
    assert detect_quota_or_rate_limit('{"type": "rate_limits", "details": "exceeded"}')
    assert detect_quota_or_rate_limit('{"type": "rate_limit_error"}')

    # Códigos de erro HTTP ou enum em bloco error
    assert detect_quota_or_rate_limit('{"error": {"code": 429, "message": "Too Many Requests"}}')
    assert detect_quota_or_rate_limit('{"error": {"code": 402, "message": "Payment Required"}}')
    assert detect_quota_or_rate_limit('{"error": {"code": "insufficient_quota", "message": "Quota exhausted"}}')
    assert detect_quota_or_rate_limit('{"error": {"code": "rate_limit_exceeded"}}')
    assert detect_quota_or_rate_limit('{"error": {"type": "insufficient_quota"}}')

    # JSON válido com mensagem de cota ancorada
    assert detect_quota_or_rate_limit('{"error": {"message": "You exceeded your current quota"}}')

    # JSON sem erro de quota
    assert not detect_quota_or_rate_limit('{"status": "ok", "result": 42}')
    assert not detect_quota_or_rate_limit('{"error": {"code": 200, "message": "success"}}')


def test_detect_quota_real_terminal_outputs():
    """Verifica detecção de mensagens reais de provedores e CLIs."""
    real_errors = [
        "HTTP 429 Too Many Requests: Rate limit exceeded",
        "google.api_core.exceptions.ResourceExhausted: 429 RESOURCE_EXHAUSTED: Quota exceeded",
        "Error: Your credit balance is too low to access the Claude API",
        "Fatal error: model 'gpt-6-luna' not found or inactive",
        "HTTP 404: unsupported model 'claude-3-ancient'",
        "Error: credit balance too low",
    ]
    for err in real_errors:
        assert detect_quota_or_rate_limit(err), f"Falha ao detectar erro real de cota: '{err}'"


# =============================================================================
# 2. Circuit Breaker Lifecycle (Achado #23)
# =============================================================================


def test_circuit_breaker_lifecycle(state_mgr):
    """Testa o ciclo de vida completo do circuit breaker: CLOSED -> OPEN -> HALF_OPEN -> CLOSED."""
    harness = "test_harness"

    # 1. Estado inicial é CLOSED e disponível
    cb = state_mgr.get_circuit_breaker(harness)
    assert cb["state"] == "CLOSED"
    assert cb["is_available"] is True
    assert state_mgr.is_harness_available(harness) is True

    # 2. Falha de cota abre o circuito imediatamente
    res = state_mgr.record_harness_failure(harness, is_quota=True, cooldown_seconds=0.05)
    assert res["state"] == "OPEN"
    assert res["is_available"] is False
    assert state_mgr.is_harness_available(harness) is False

    cb_open = state_mgr.get_circuit_breaker(harness)
    assert cb_open["state"] == "OPEN"
    assert cb_open["is_available"] is False

    # 3. Aguarda expiração do cooldown -> deve transitar para HALF_OPEN
    time.sleep(0.06)
    cb_half = state_mgr.get_circuit_breaker(harness)
    assert cb_half["state"] == "HALF_OPEN"
    assert cb_half["is_available"] is True
    assert state_mgr.is_harness_available(harness) is True

    # 4. Sucesso no harness fecha o circuito e zera falhas
    rec_ok = state_mgr.record_harness_success(harness)
    assert rec_ok["state"] == "CLOSED"
    assert rec_ok["failure_count"] == 0
    assert rec_ok["success_count"] >= 1
    assert rec_ok["is_available"] is True

    cb_closed = state_mgr.get_circuit_breaker(harness)
    assert cb_closed["state"] == "CLOSED"
    assert cb_closed["is_available"] is True


def test_circuit_breaker_non_quota_threshold(state_mgr):
    """Verifica que falhas que não são de cota só abrem o circuito após atingir o failure_threshold."""
    harness = "worker_native"

    # 1ª falha não-quota com threshold=2 -> circuito permanece CLOSED
    res1 = state_mgr.record_harness_failure(harness, is_quota=False, failure_threshold=2)
    assert res1["state"] == "CLOSED"
    assert res1["is_available"] is True
    assert res1["failure_count"] == 1

    # 2ª falha atinge o threshold -> circuito abre (OPEN)
    res2 = state_mgr.record_harness_failure(harness, is_quota=False, failure_threshold=2)
    assert res2["state"] == "OPEN"
    assert res2["is_available"] is False
    assert res2["failure_count"] == 2


def test_circuit_breaker_reset(state_mgr):
    """Verifica reset completo de todos os circuit breakers."""
    state_mgr.record_harness_failure("h1", is_quota=True)
    state_mgr.record_harness_failure("h2", is_quota=True)
    assert state_mgr.is_harness_available("h1") is False
    assert state_mgr.is_harness_available("h2") is False

    state_mgr.reset_circuit_breakers()
    assert state_mgr.is_harness_available("h1") is True
    assert state_mgr.is_harness_available("h2") is True


# =============================================================================
# 3. Quota Accounting por Janela Temporal (Achado #23)
# =============================================================================


def test_quota_accounting_metrics(state_mgr):
    """Verifica contabilidade agregada de tokens, requisições e custo por janela temporal."""
    now = time.time()

    # Registra uso atual para dois modelos/harnesses
    state_mgr.record_quota_usage(
        harness="luna",
        model="gpt-6-luna",
        tokens_in=1000,
        tokens_out=200,
        cost=0.0001,
        timestamp=now - 5,
    )
    state_mgr.record_quota_usage(
        harness="luna",
        model="gpt-6-luna",
        tokens_in=1500,
        tokens_out=300,
        cost=0.00015,
        timestamp=now - 2,
    )
    state_mgr.record_quota_usage(
        harness="haiku",
        model="claude-3-haiku",
        tokens_in=3000,
        tokens_out=500,
        cost=0.002,
        timestamp=now - 1,
    )
    # Registro antigo (fora da janela de 60s)
    state_mgr.record_quota_usage(
        harness="luna",
        model="gpt-6-luna",
        tokens_in=50000,
        tokens_out=10000,
        cost=0.05,
        timestamp=now - 120,
    )

    # 1. Agregado para "luna" na janela de 60s
    luna_usage = state_mgr.get_quota_usage(harness="luna", window_seconds=60)
    assert luna_usage["request_count"] == 2
    assert luna_usage["tokens_in"] == 2500
    assert luna_usage["tokens_out"] == 500
    assert pytest.approx(luna_usage["cost"], 1e-6) == 0.00025

    # 2. Agregado para "all" na janela de 60s
    all_usage = state_mgr.get_quota_usage(window_seconds=60)
    assert all_usage["request_count"] == 3
    assert all_usage["tokens_in"] == 5500
    assert all_usage["tokens_out"] == 1000
    assert pytest.approx(all_usage["cost"], 1e-6) == 0.00225

    # 3. Janela ampla de 300s inclui o registro antigo
    old_usage = state_mgr.get_quota_usage(harness="luna", window_seconds=300)
    assert old_usage["request_count"] == 3
    assert old_usage["tokens_in"] == 52500


# =============================================================================
# 4. WorkerSpawner com Circuit Breaker (get_next_available_tier)
# =============================================================================


def test_spawner_skips_in_cooldown_tiers(state_mgr):
    """Verifica que WorkerSpawner.get_next_available_tier pula tiers cujo circuit breaker está OPEN."""
    config = MeisterConfig(
        workers=WorkersConfig(
            tier_order=[
                WorkerTier(name="luna", harness="codex"),
                WorkerTier(name="gemini_flash", harness="agy"),
                WorkerTier(name="haiku", harness="claude"),
                WorkerTier(name="sonnet", harness="claude"),
            ]
        )
    )
    spawner = WorkerSpawner(config=config)

    # Se nenhum está em cooldown, o próximo após luna é gemini_flash
    assert spawner.get_next_available_tier("luna", state_manager=state_mgr).name == "gemini_flash"

    # Abre circuit breaker para gemini_flash
    state_mgr.record_harness_failure("gemini_flash", is_quota=True)
    assert state_mgr.is_harness_available("gemini_flash") is False

    # Agora, o próximo após luna deve pular gemini_flash e escolher haiku
    next_tier = spawner.get_next_available_tier("luna", state_manager=state_mgr)
    assert next_tier is not None
    assert next_tier.name == "haiku"

    # Abre circuit breaker também para haiku e sonnet
    state_mgr.record_harness_failure("haiku", is_quota=True)
    state_mgr.record_harness_failure("sonnet", is_quota=True)

    # Todos os tiers superiores estão em cooldown -> retorna None
    assert spawner.get_next_available_tier("luna", state_manager=state_mgr) is None


# =============================================================================
# 5. HerdrEventBridge Integration: Quota Backoff & Accounting (Achados #22, #23)
# =============================================================================


@pytest.mark.asyncio
async def test_bridge_circuit_breaker_trips_and_records_usage(tmp_path):
    """Testa integração no HerdrEventBridge: falha 429 abre circuit breaker, escala para tier livre e grava contabilidade."""
    db_file = tmp_path / "bridge_cb.db"
    sm = StateManager(db_path=str(db_file))

    config = MeisterConfig(
        workers=WorkersConfig(
            tier_order=[
                WorkerTier(name="luna", harness="codex"),
                WorkerTier(name="gemini_flash", harness="agy"),
            ]
        ),
        concurrency=ConcurrencyConfig(layout_strategy="tiled"),
    )

    mock_client = AsyncMock()

    from meister.worker import write_atomic_json, read_atomic_json

    attempt = 0
    async def fake_split(*args, **kwargs):
        nonlocal attempt
        attempt += 1
        cmd = kwargs.get("command")
        if cmd and len(cmd) >= 5 and cmd[-2] == "run-task":
            tfile = cmd[-1]
            data = read_atomic_json(tfile)
            if data and "result_file" in data:
                if attempt == 1:
                    res = {"status": "error", "output": "HTTP 429 Too Many Requests: Rate limit exceeded"}
                else:
                    res = {
                        "status": "done",
                        "output": "Implementation complete",
                        "usage": {"prompt_tokens": 120, "completion_tokens": 40, "cost": 0.00015},
                    }
                write_atomic_json(data["result_file"], res)
        return f"w1:p{attempt}"

    mock_client.split_pane.side_effect = fake_split
    mock_client.read_pane.side_effect = [
        "HTTP 429 Too Many Requests: Rate limit exceeded",
        "Implementation complete",
    ]

    bridge = HerdrEventBridge(config=config, client=mock_client, state_manager=sm)
    subtask = {"id": "sub_quota_test", "description": "Fix auth", "target_files": ["auth.py"], "cwd": str(tmp_path)}

    # Executa subtask
    with patch.dict(os.environ, {"MEISTER_RETRY_BACKOFF": "0.001"}):
        success = await bridge.execute_subtask(subtask)

    assert success is True

    # 1. Verifica que o circuit breaker para 'luna' foi aberto (OPEN)
    luna_cb = sm.get_circuit_breaker("luna")
    assert luna_cb["state"] == "OPEN"
    assert sm.is_harness_available("luna") is False

    # 2. Verifica que 'gemini_flash' foi registrado com sucesso
    gemini_cb = sm.get_circuit_breaker("gemini_flash")
    assert gemini_cb["state"] == "CLOSED"
    assert gemini_cb["success_count"] >= 1

    # 3. Verifica contabilidade de cota gravada para 'gemini_flash'
    usage = sm.get_quota_usage(harness="gemini_flash", window_seconds=60)
    assert usage["request_count"] == 1
    assert usage["tokens_in"] == 120
    assert usage["tokens_out"] == 40
    assert pytest.approx(usage["cost"], 1e-6) == 0.00015

    sm.close()
