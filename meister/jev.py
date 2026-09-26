"""
meister.jev — Cliente da Decisions API (TypeSafe Jev-1.13 via OpenRouter).

Executa chamadas tipadas para classificação e controle do ciclo de desenvolvimento.
Nunca gera texto livre; opera exclusivamente via probabilidades e valores tipados.
"""

import os
import sys
import uuid
import requests
from typing import Dict, Any, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv(os.path.expanduser("~/.meister/.env"))
except ImportError:
    pass

from meister.logger import log_classify, log_control

OPENROUTER_URL = os.environ.get(
    "OPENROUTER_DECISIONS_URL",
    "https://openrouter.ai/api/alpha/decisions"
)
DEFAULT_MODEL = os.environ.get("JEV_MODEL", "typesafe/jev-1.13")


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
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("OPENROUTER_API_KEY="):
                            key = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if key:
                                os.environ["OPENROUTER_API_KEY"] = key
                                break
            if key:
                break

    if not key:
        raise ValueError(
            "OPENROUTER_API_KEY não encontrada no ambiente ou .env. "
            "Configure a variável no seu shell ou crie um arquivo ~/.meister/.env."
        )
    return key


def call_decisions(state: dict, questions: dict, model: Optional[str] = None) -> Dict[str, Any]:
    """Envia requisição tipada para a Decisions API da TypeSafe via OpenRouter."""
    api_key = get_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/CristianonCarvalho/meisterrouter",
        "X-Title": "MeisterRouter Orchestration Engine",
    }
    payload = {
        "model": model or DEFAULT_MODEL,
        "state": state,
        "questions": questions,
    }

    try:
        resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if "answers" not in data:
            raise RuntimeError(f"Resposta inesperada do Jev (sem campo 'answers'): {data}")
        return data
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Erro na requisição para OpenRouter Decisions API: {e}")


def classify_task(context: str, model: Optional[str] = None) -> Dict[str, Any]:
    """
    Classifica a complexidade da tarefa e sugere o subagente ideal.
    """
    task_id = str(uuid.uuid4())
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
            "criteria": {
                "luna": "GPT-6 Luna: $0.077/M, altíssima eficiência e inteligência 29 para tarefas small/medium.",
                "haiku": "Claude 4.5 Haiku: $0.77/M, subagente lite rápido.",
                "gemini_antigravity": "Gemini 3.8 Flash: $0.577/M, líder de automação e raciocínio profundo para código difícil.",
            },
        },
    }

    raw = call_decisions(state=state, questions=questions, model=model)
    answers = raw.get("answers", {})

    cls_ans = answers.get("complexity", {})
    impl_ans = answers.get("recommended_implementer", {})

    cls_val = cls_ans.get("choice", "medium").upper()
    cls_conf = cls_ans.get("confidence", 0.8)
    cls_probs = cls_ans.get("probabilities", {})

    impl_val = impl_ans.get("choice", "luna").lower()
    impl_conf = impl_ans.get("confidence", 0.8)

    # Se a complexidade for SMALL ou MEDIUM e o implementador sugerido for genérico,
    # assegura direcionamento ao campeão de custo/inteligência GPT-6 Luna
    if cls_val in ["SMALL", "MEDIUM"] and impl_val in ["haiku", "luna"]:
        impl_val = "luna"

    # Registra no log de telemetria
    usage = raw.get("usage", {})
    tokens_in = usage.get("prompt_tokens", 0)
    tokens_out = usage.get("completion_tokens", 0)

    log_classify(
        task_id=task_id,
        context=context,
        classification=cls_val,
        recommended_implementer=impl_val,
        confidence=cls_conf,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )

    return {
        "task_id": task_id,
        "classification": cls_val,
        "classification_confidence": round(cls_conf, 2),
        "classification_probabilities": cls_probs,
        "recommended_implementer": impl_val,
        "implementer_confidence": round(impl_conf, 2),
    }


def control_cycle(diff_summary: str, test_result: str, attempts: int = 1,
                  security_sensitive: bool = False, model: Optional[str] = None) -> Dict[str, Any]:
    """
    Avalia o progresso determinístico e decide a próxima ação do loop do agente.
    """
    task_id = str(uuid.uuid4())
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
            "instructions": "Deveria trocar o subagente de implementação (ex: de Luna/Haiku para Gemini 3.8 Flash) antes de escalar?",
            "criteria": {
                "true": "O subagente atual falhou de forma consistente com evidência de que um mais potente ajudaria.",
                "false": "Não há motivo para trocar de subagente ainda.",
            },
        },
    }

    raw = call_decisions(state=state, questions=questions, model=model)
    answers = raw.get("answers", {})

    act_ans = answers.get("next_action", {})
    esc_ans = answers.get("should_escalate", {})
    sw_ans = answers.get("switch_implementer", {})

    act_val = act_ans.get("choice", "verify").upper()
    act_conf = act_ans.get("confidence", 0.8)

    esc_val = esc_ans.get("noul", 0.0) > 0.5
    esc_prob = esc_ans.get("noul", 0.0)

    sw_val = sw_ans.get("noul", 0.0) > 0.5
    sw_prob = sw_ans.get("noul", 0.0)

    usage = raw.get("usage", {})
    tokens_in = usage.get("prompt_tokens", 0)
    tokens_out = usage.get("completion_tokens", 0)

    log_control(
        task_id=task_id,
        action=act_val,
        should_escalate=esc_val,
        switch_implementer=sw_val,
        confidence=act_conf,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )

    return {
        "task_id": task_id,
        "action": act_val,
        "action_confidence": round(act_conf, 2),
        "should_escalate": esc_val,
        "escalate_probability": round(esc_prob, 2),
        "switch_implementer": sw_val,
        "switch_implementer_probability": round(sw_prob, 2),
    }
