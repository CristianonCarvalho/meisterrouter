"""
meister.jev — Cliente da Decisions API (TypeSafe Jev-1.13 via OpenRouter).

Executa chamadas tipadas para classificação e controle do ciclo de desenvolvimento.
Nunca gera texto livre; opera exclusivamente via probabilidades e valores tipados.

Melhorias determinísticas (Achados #24, #26, #27, #28):
- Validação estrita via Pydantic schemas.
- Retries automáticos com backoff exponencial (3 tentativas).
- Fallback determinístico por regras em caso de indisponibilidade da API.
- Cache de respostas baseado no hash do input (determinismo estrito).
- Leitura precisa de tokens e usage.cost.
- Envio explícito de temperature: 0.0.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

import requests
from pydantic import BaseModel, Field, ValidationError

try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv(os.path.expanduser("~/.meister/.env"))
except ImportError:
    pass

from meister.logger import log_classify, log_control, get_current_run, save_current_run
from meister.config import WorkerTier, load_config

logger = logging.getLogger(__name__)

OPENROUTER_URL = os.environ.get(
    "OPENROUTER_DECISIONS_URL",
    "https://openrouter.ai/api/alpha/decisions"
)
# Cache determinístico em memória por hash SHA-256 do payload (Achado #28)
_DECISIONS_CACHE: Dict[str, Dict[str, Any]] = {}


def clear_decisions_cache() -> None:
    """Limpa o cache em memória das decisões do Jev."""
    _DECISIONS_CACHE.clear()


def get_decisions_cache() -> Dict[str, Dict[str, Any]]:
    """Retorna o cache atual de decisões."""
    return _DECISIONS_CACHE


# ─── Pydantic Schemas (Achado #24) ──────────────────────────────────────────

class ComplexityLevel(str, Enum):
    SMALL = "SMALL"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    ESCALATE = "ESCALATE"


class ControlAction(str, Enum):
    CONTINUE = "CONTINUE"
    RETRY = "RETRY"
    VERIFY = "VERIFY"
    ESCALATE = "ESCALATE"
    COMPLETE = "COMPLETE"


class DecisionsAnswer(BaseModel):
    choice: Optional[str] = None
    confidence: float = 0.8
    probabilities: Dict[str, float] = Field(default_factory=dict)
    noul: Optional[float] = None


class DecisionsUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost: Optional[float] = None
    total_cost: Optional[float] = None


class DecisionsResponse(BaseModel):
    answers: Dict[str, DecisionsAnswer]
    usage: Optional[DecisionsUsage] = None


class ClassifyResponse(BaseModel):
    task_id: str
    run_id: Optional[str] = None
    classification: str
    classification_confidence: Optional[float] = None
    classification_probabilities: Dict[str, float]
    recommended_implementer: str
    fallback_chain: List[str]
    implementer_confidence: Optional[float] = None
    fallback_rule_applied: bool = False
    cost: float = 0.0


class ControlResponse(BaseModel):
    task_id: str
    run_id: Optional[str] = None
    action: str
    action_confidence: Optional[float] = None
    should_escalate: bool
    escalate_probability: float
    switch_implementer: bool
    switch_implementer_probability: float
    fallback_rule_applied: bool = False
    cost: float = 0.0


# ─── Funções Principais ──────────────────────────────────────────────────────

def get_api_key() -> str:
    """Recupera a chave OPENROUTER_API_KEY do ambiente ou de arquivos .env."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        env_paths = [
            os.path.join(os.getcwd(), ".env"),
            os.path.expanduser("~/.meister/.env"),
            os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
        ]
        for p in env_paths:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith("OPENROUTER_API_KEY="):
                                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                                if key:
                                    os.environ["OPENROUTER_API_KEY"] = key
                                    break
                except Exception:
                    pass
            if key:
                break

    if not key:
        raise ValueError(
            "OPENROUTER_API_KEY não encontrada no ambiente ou .env. "
            "Configure a variável no seu shell ou crie um arquivo ~/.meister/.env."
        )
    return key


def compute_payload_hash(payload: Dict[str, Any]) -> str:
    """Calcula hash SHA-256 determinístico para payload de requisição."""
    serialized = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def call_decisions(
    state: dict,
    questions: dict,
    model: Optional[str] = None,
    max_retries: int = 3,
    timeout: int = 20,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """Envia requisição tipada para a Decisions API da TypeSafe via OpenRouter.

    Implementa:
    - Cache em memória por hash do input (Achado #28).
    - Envio de temperature: 0.0 (Achado #24).
    - Até 3 retries com backoff exponencial em caso de falha de conexão/5xx (Achado #24).
    - Validação de schema Pydantic da resposta (Achado #24).
    """
    model_name = model or load_config().master.model
    payload: Dict[str, Any] = {
        "model": model_name,
        "temperature": 0.0,
        "state": state,
        "questions": questions,
    }

    cache_key = compute_payload_hash(payload)
    if use_cache and cache_key in _DECISIONS_CACHE:
        logger.debug("Decisions cache hit for payload hash %s", cache_key)
        return _DECISIONS_CACHE[cache_key]

    api_key = get_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/CristianonCarvalho/meisterrouter",
        "X-Title": "MeisterRouter Orchestration Engine",
    }

    last_error: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=timeout)
            if resp.status_code >= 500:
                raise requests.exceptions.HTTPError(f"OpenRouter Server Error {resp.status_code}", response=resp)
            resp.raise_for_status()
            data = resp.json()

            # Valida estrutura via Pydantic
            try:
                DecisionsResponse.model_validate(data)
            except ValidationError as ve:
                raise RuntimeError(f"Resposta malformada da Decisions API (Pydantic validation): {ve}")

            if "answers" not in data:
                raise RuntimeError(f"Resposta inesperada do Jev (sem campo 'answers'): {data}")

            if use_cache:
                _DECISIONS_CACHE[cache_key] = data

            return data
        except (requests.exceptions.RequestException, RuntimeError) as e:
            last_error = e
            logger.warning("Tentativa %d/%d falhou para Decisions API: %s", attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                time.sleep(0.3 * (2 ** attempt))

    raise RuntimeError(f"Erro na requisição para OpenRouter Decisions API após {max_retries} tentativas: {last_error}")


def classify_task(
    context: str,
    model: Optional[str] = None,
    task_id: Optional[str] = None,
    use_cache: bool = True,
    run_id: Optional[str] = None,
    attempt: int = 1,
    implementers: Optional[Sequence[WorkerTier]] = None,
) -> Dict[str, Any]:
    """Classifica a complexidade da tarefa e sugere o subagente ideal.

    Implementa:
    - ID determinístico se não especificado (Achado #28).
    - Extração correta de tokens e usage.cost (Achado #26).
    - Fallback determinístico por regras se a API falhar (Achado #24).
    - Validação de saída com Pydantic ClassifyResponse (Achado #24).
    """
    start_time = time.monotonic()
    current = get_current_run()
    resolved_run_id = (
        run_id
        or os.environ.get("MEISTER_RUN_ID")
        or current.get("run_id")
        or f"run_{uuid.uuid4().hex[:8]}"
    )
    safe_task_id = task_id or hashlib.sha256(f"classify:{context}".encode("utf-8")).hexdigest()[:16]
    save_current_run(run_id=resolved_run_id, task_id=safe_task_id)
    decision_model = model or load_config().master.model
    configured_implementers = list(
        load_config().workers.tier_order if implementers is None else implementers
    )
    if not configured_implementers:
        raise ValueError("implementers não pode ser uma sequência vazia")

    implementer_names = [tier.name for tier in configured_implementers]
    implementer_criteria = {}
    for tier in configured_implementers:
        description_parts = [tier.model or tier.harness, f"via {tier.harness}" if tier.harness else ""]
        if tier.cost_per_m_tokens:
            description_parts.append(f"(${tier.cost_per_m_tokens}/M)")
        description = " ".join(part for part in description_parts if part)
        if tier.best_for:
            description += f": {', '.join(tier.best_for)}"
        implementer_criteria[tier.name] = description

    state = {"task_description": context}
    questions = {
        "complexity": {
            "type": "choice",
            "instructions": "Qual o nível de complexidade e risco desta tarefa de engenharia de software?",
            "criteria": {
                "small": "Mudança trivial, escopo muito limitado, baixo risco.",
                "medium": "Mudança de escopo moderado, alguma lógica nova.",
                "high": "Mudança complexa, múltiplos arquivos, lógica não trivial.",
                "escalate": "Alta incerteza arquitetural, risco de segurança, ou escopo muito amplo.",
            },
        },
        "recommended_implementer": {
            "type": "choice",
            "instructions": "Qual subagente de implementação é mais adequado considerando custo e precisão?",
            "criteria": implementer_criteria,
        },
    }

    fallback_applied = False
    cost = 0.0
    cls_conf: Optional[float] = None
    impl_conf: Optional[float] = None

    try:
        raw = call_decisions(state=state, questions=questions, model=decision_model, use_cache=use_cache)
        answers = raw.get("answers", {})

        cls_ans = answers.get("complexity", {})
        impl_ans = answers.get("recommended_implementer", {})

        cls_val = str(cls_ans.get("choice", "medium")).upper()
        cls_conf = float(cls_ans.get("confidence", 0.8))
        cls_probs = dict(cls_ans.get("probabilities", {}))

        raw_impl_val = impl_ans.get("choice")
        if raw_impl_val is None:
            impl_val = implementer_names[0]
            fallback_applied = True
        else:
            impl_val = str(raw_impl_val)
        impl_conf = float(impl_ans.get("confidence", 0.8))

        usage = raw.get("usage", {})
        tokens_in = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
        tokens_out = usage.get("output_tokens") or usage.get("completion_tokens") or 0
        cost = float(usage.get("cost") or usage.get("total_cost") or 0.0)

    except Exception as e:
        logger.warning("Decisions API indisponível (%s). Aplicando tabela determinística de regras (Achado #24).", e)
        fallback_applied = True
        cls_probs = {}
        cls_conf = None
        impl_conf = None
        tokens_in = 0
        tokens_out = 0

        ctx_lower = context.lower()
        if any(w in ctx_lower for w in ["security", "auth", "crypto", "architect", "vulnerability", "refactor all", "migration"]):
            cls_val = "HIGH"
            impl_val = implementer_names[0]
        elif any(w in ctx_lower for w in ["typo", "fix doc", "readme", "comment", "format", "rename", "tiny", "trivial"]):
            cls_val = "SMALL"
            impl_val = implementer_names[0]
        else:
            cls_val = "MEDIUM"
            impl_val = implementer_names[0]

    # Normalização de escolhas fora do enum padrão
    valid_complexities = {c.value for c in ComplexityLevel}
    if cls_val not in valid_complexities:
        cls_val = "MEDIUM"

    valid_implementers = set(implementer_names)
    if impl_val not in valid_implementers:
        impl_val = implementer_names[0]
        fallback_applied = True

    # Define a cadeia determinística de fallback se o modelo recomendado não estiver ativo
    selected_index = implementer_names.index(impl_val)
    fallback_chain = implementer_names[selected_index + 1:]

    duration_ms = round((time.monotonic() - start_time) * 1000.0, 2)
    # Registra no log de telemetria com custo real e duração (Achado #26, E2E-6)
    log_classify(
        task_id=safe_task_id,
        context=context,
        classification=cls_val,
        recommended_implementer=impl_val,
        confidence=cls_conf,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost=cost,
        run_id=resolved_run_id,
        attempt=attempt,
        duration_ms=duration_ms,
        model=decision_model,
    )

    result_dict = {
        "run_id": resolved_run_id,
        "task_id": safe_task_id,
        "classification": cls_val,
        "classification_confidence": round(cls_conf, 2) if cls_conf is not None else None,
        "classification_probabilities": cls_probs,
        "recommended_implementer": impl_val,
        "fallback_chain": fallback_chain,
        "implementer_confidence": round(impl_conf, 2) if impl_conf is not None else None,
        "fallback_rule_applied": fallback_applied,
        "cost": round(cost, 6),
    }

    # Validação do contrato via Pydantic
    ClassifyResponse.model_validate(result_dict)
    return result_dict


def control_cycle(
    diff_summary: str,
    test_result: str,
    attempts: int = 1,
    security_sensitive: bool = False,
    model: Optional[str] = None,
    task_id: Optional[str] = None,
    use_cache: bool = True,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Avalia o progresso determinístico e decide a próxima ação do loop do agente.

    Implementa:
    - ID determinístico se não especificado (Achado #28).
    - Extração correta de tokens e usage.cost (Achado #26).
    - Portão determinístico hard: se testes falharem, NUNCA permite COMPLETE (Achado #4).
    - Fallback determinístico por regras se a API falhar (Achado #24).
    - Validação de saída com Pydantic ControlResponse (Achado #24).
    """
    start_time = time.monotonic()
    current = get_current_run()
    resolved_run_id = (
        run_id
        or os.environ.get("MEISTER_RUN_ID")
        or current.get("run_id")
        or f"run_{uuid.uuid4().hex[:8]}"
    )
    safe_task_id = (
        task_id
        or current.get("task_id")
        or hashlib.sha256(
            f"control:{diff_summary}:{test_result}:{attempts}:{security_sensitive}".encode("utf-8")
        ).hexdigest()[:16]
    )
    decision_model = model or load_config().master.model

    state = {
        "diff_summary": diff_summary,
        "test_result": test_result,
        "attempts_so_far": attempts,
        "security_sensitive": security_sensitive,
    }
    questions = {
        "next_action": {
            "type": "choice",
            "instructions": "Com base na evidência determinística coletada, qual a próxima ação no loop do agente?",
            "criteria": {
                "continue": "Progresso adequado; seguir implementando dentro do escopo.",
                "retry": "Falha recuperável; tentar novamente a mesma etapa.",
                "verify": "Implementação parece completa mas precisa de checagem adicional.",
                "escalate": "Falhas repetidas, incerteza arquitetural, ou risco de segurança — subir de nível/modelo.",
                "complete": "Comportamento implementado, checagens passaram, escopo atendido.",
            },
        },
        "should_escalate": {
            "type": "noul",
            "instructions": "A evidência atual justifica escalar para um modelo de raciocínio mais forte?",
            "criteria": {
                "true": "Tentativas repetidas falharam, testes continuam falhando, ou há incerteza arquitetural.",
                "false": "O nível atual de modelo ainda é suficiente.",
            },
        },
        "switch_implementer": {
            "type": "noul",
            "instructions": "Deveria trocar o subagente de implementação antes de escalar?",
            "criteria": {
                "true": "O subagente atual falhou de forma consistente com evidência de que um mais potente ajudaria.",
                "false": "Não há motivo para trocar de subagente ainda.",
            },
        },
    }

    fallback_applied = False
    cost = 0.0
    act_conf: Optional[float] = None

    try:
        raw = call_decisions(state=state, questions=questions, model=decision_model, use_cache=use_cache)
        answers = raw.get("answers", {})

        act_ans = answers.get("next_action", {})
        esc_ans = answers.get("should_escalate", {})
        sw_ans = answers.get("switch_implementer", {})

        act_val = str(act_ans.get("choice", "verify")).upper()
        act_conf = float(act_ans.get("confidence", 0.8))

        esc_val = esc_ans.get("noul", 0.0) > 0.5
        esc_prob = float(esc_ans.get("noul", 0.0))

        sw_val = sw_ans.get("noul", 0.0) > 0.5
        sw_prob = float(sw_ans.get("noul", 0.0))

        usage = raw.get("usage", {})
        tokens_in = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
        tokens_out = usage.get("output_tokens") or usage.get("completion_tokens") or 0
        cost = float(usage.get("cost") or usage.get("total_cost") or 0.0)

    except Exception as e:
        logger.warning("Decisions API indisponível (%s). Aplicando tabela determinística de controle por regras (Achado #24).", e)
        fallback_applied = True
        act_conf = None
        tokens_in = 0
        tokens_out = 0

        if test_result.lower() == "pass":
            act_val = "COMPLETE"
            esc_val = False
            esc_prob = 0.05
            sw_val = False
            sw_prob = 0.05
        else:
            if attempts >= 2 or security_sensitive:
                act_val = "ESCALATE"
                esc_val = True
                esc_prob = 0.90
                sw_val = True
                sw_prob = 0.85
            else:
                act_val = "RETRY"
                esc_val = False
                esc_prob = 0.20
                sw_val = False
                sw_prob = 0.20

    # Normalização de ação fora do enum
    valid_actions = {a.value for a in ControlAction}
    if act_val not in valid_actions:
        act_val = "VERIFY"

    # Portão determinístico hard: se testes falharem, NUNCA permite COMPLETE (Achado #4)
    if test_result.lower() != "pass" and act_val == "COMPLETE":
        logger.warning("Jev retornou COMPLETE com testes falhando. Sobrescrevendo para RETRY via portão determinístico.")
        act_val = "RETRY"

    duration_ms = round((time.monotonic() - start_time) * 1000.0, 2)
    # Registra no log de telemetria com custo real e duração (Achado #26, E2E-6)
    log_control(
        task_id=safe_task_id,
        action=act_val,
        should_escalate=esc_val,
        switch_implementer=sw_val,
        confidence=act_conf,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost=cost,
        run_id=resolved_run_id,
        attempt=attempts,
        duration_ms=duration_ms,
        model=decision_model,
    )

    result_dict = {
        "run_id": resolved_run_id,
        "task_id": safe_task_id,
        "action": act_val,
        "action_confidence": round(act_conf, 2) if act_conf is not None else None,
        "should_escalate": esc_val,
        "escalate_probability": round(esc_prob, 2),
        "switch_implementer": sw_val,
        "switch_implementer_probability": round(sw_prob, 2),
        "fallback_rule_applied": fallback_applied,
        "cost": round(cost, 6),
    }

    # Validação do contrato via Pydantic
    ControlResponse.model_validate(result_dict)
    return result_dict
