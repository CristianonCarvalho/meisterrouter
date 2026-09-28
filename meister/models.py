"""
meister.models — Catálogo de modelos, precificação e métricas do Artificial Analysis.

Define a matriz de inteligência, limites de contexto, custos por milhão de tokens
e suporte para o roteamento do MeisterRouter.
"""

from typing import Dict, Any

# Preços em USD por 1 milhão de tokens (conforme Artificial Analysis e tabelas oficiais 2026)
MODEL_PRICING: Dict[str, Dict[str, Any]] = {
    # 1. Primary Low-Cost Implementer (Disruptivo)
    "openai/gpt-6-luna": {
        "name": "GPT-6 Luna (medium)",
        "provider": "OpenAI",
        "input": 0.10,
        "output": 0.50,
        "cache_read": 0.01,
        "intelligence_index": 29,
        "terminal_bench": 3,
        "automation_bench": 40,
        "scicode": 51,
        "role": "primary_implementer",
    },
    "luna": {  # Alias
        "name": "GPT-6 Luna (medium)",
        "provider": "OpenAI",
        "input": 0.10,
        "output": 0.50,
        "cache_read": 0.01,
        "intelligence_index": 29,
        "terminal_bench": 3,
        "automation_bench": 40,
        "scicode": 51,
        "role": "primary_implementer",
    },

    # 2. Secondary Low-Cost Subagent
    "anthropic/claude-3-5-haiku": {
        "name": "Claude 4.5 Haiku (Reasoning)",
        "provider": "Anthropic",
        "input": 1.00,
        "output": 5.00,
        "cache_read": 0.10,
        "intelligence_index": 17,
        "terminal_bench": 0,
        "automation_bench": 3,
        "scicode": 42,
        "role": "secondary_implementer",
    },
    "haiku-4.5": {  # Alias
        "name": "Claude 4.5 Haiku (Reasoning)",
        "provider": "Anthropic",
        "input": 1.00,
        "output": 5.00,
        "cache_read": 0.10,
        "intelligence_index": 17,
        "terminal_bench": 0,
        "automation_bench": 3,
        "scicode": 42,
        "role": "secondary_implementer",
    },

    # 3. Premier Deep Reasoning / Escalation Implementer
    "google/gemini-2.5-flash": {
        "name": "Gemini 3.8 Flash (medium)",
        "provider": "Google",
        "input": 0.75,
        "output": 3.75,
        "cache_read": 0.075,
        "intelligence_index": 40,
        "terminal_bench": 20,
        "automation_bench": 61,
        "scicode": 55,
        "role": "deep_reasoning_escalation",
    },
    "gemini-3.8-flash": {  # Alias
        "name": "Gemini 3.8 Flash (medium)",
        "provider": "Google",
        "input": 0.75,
        "output": 3.75,
        "cache_read": 0.075,
        "intelligence_index": 40,
        "terminal_bench": 20,
        "automation_bench": 61,
        "scicode": 55,
        "role": "deep_reasoning_escalation",
    },
    "gemini-3.8-antigravity": {  # Alias
        "name": "Gemini 3.8 Flash (medium)",
        "provider": "Google",
        "input": 0.75,
        "output": 3.75,
        "cache_read": 0.075,
        "intelligence_index": 40,
        "terminal_bench": 20,
        "automation_bench": 61,
        "scicode": 55,
        "role": "deep_reasoning_escalation",
    },

    # 4. Master Architect (Claude Code)
    "anthropic/claude-sonnet-5": {
        "name": "Claude Sonnet 5 (Adaptive Reasoning)",
        "provider": "Anthropic",
        "input": 2.00,
        "output": 10.00,
        "cache_read": 0.20,
        "intelligence_index": 28,
        "terminal_bench": 2,
        "automation_bench": 28,
        "scicode": 52,
        "role": "architect",
    },
    "anthropic/claude-3-7-sonnet": {  # Deprecated alias
        "name": "Claude Sonnet 5 (Adaptive Reasoning)",
        "provider": "Anthropic",
        "input": 2.00,
        "output": 10.00,
        "cache_read": 0.20,
        "intelligence_index": 28,
        "terminal_bench": 2,
        "automation_bench": 28,
        "scicode": 52,
        "role": "architect",
    },
    "sonnet-5": {  # Alias
        "name": "Claude Sonnet 5 (Adaptive Reasoning)",
        "provider": "Anthropic",
        "input": 2.00,
        "output": 10.00,
        "cache_read": 0.20,
        "intelligence_index": 28,
        "terminal_bench": 2,
        "automation_bench": 28,
        "scicode": 52,
        "role": "architect",
    },

    # 5. Master Architect (Codex)
    "openai/gpt-4o": {
        "name": "OpenAI Codex / GPT-4o",
        "provider": "OpenAI",
        "input": 2.50,
        "output": 10.00,
        "cache_read": 1.25,
        "intelligence_index": 30,
        "terminal_bench": 12,
        "automation_bench": 35,
        "scicode": 50,
        "role": "architect",
    },
    "codex": {  # Alias
        "name": "OpenAI Codex / GPT-4o",
        "provider": "OpenAI",
        "input": 2.50,
        "output": 10.00,
        "cache_read": 1.25,
        "intelligence_index": 30,
        "terminal_bench": 12,
        "automation_bench": 35,
        "scicode": 50,
        "role": "architect",
    },

    # 6. Maximum Escalation
    "anthropic/claude-3-opus": {
        "name": "Claude Opus 5.5",
        "provider": "Anthropic",
        "input": 15.00,
        "output": 75.00,
        "cache_read": 1.50,
        "intelligence_index": 35,
        "terminal_bench": 15,
        "automation_bench": 45,
        "scicode": 54,
        "role": "maximum_escalation",
    },
    "opus-5.5": {  # Alias
        "name": "Claude Opus 5.5",
        "provider": "Anthropic",
        "input": 15.00,
        "output": 75.00,
        "cache_read": 1.50,
        "intelligence_index": 35,
        "terminal_bench": 15,
        "automation_bench": 45,
        "scicode": 54,
        "role": "maximum_escalation",
    },

    # 7. TypeSafe Decision Router
    "typesafe/jev-1.13": {
        "name": "Jev Decisions 1.13",
        "provider": "TypeSafe System One",
        "input": 0.50,
        "output": 0.50,
        "cache_read": 0.05,
        "intelligence_index": 45,  # Especialista em decisões tipadas
        "role": "decision_router",
    },
}


def get_model_info(model_key: str) -> Dict[str, Any]:
    """Retorna metadados e preços de um modelo."""
    key = model_key.lower().strip()
    return MODEL_PRICING.get(key, {
        "name": model_key,
        "provider": "Unknown",
        "input": 1.0,
        "output": 3.0,
        "cache_read": 0.1,
        "role": "general",
    })


def estimate_cost(model_key: str, tokens_in: int = 0, tokens_out: int = 0, cache_read_tokens: int = 0) -> float:
    """Calcula o custo estimado em USD com base no volume de tokens."""
    info = get_model_info(model_key)
    cost = (
        (tokens_in / 1_000_000) * info.get("input", 1.0)
        + (tokens_out / 1_000_000) * info.get("output", 3.0)
        + (cache_read_tokens / 1_000_000) * info.get("cache_read", 0.1)
    )
    return round(cost, 6)
