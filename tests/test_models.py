import pytest
from meister.models import get_model_info, estimate_cost, MODEL_PRICING

def test_get_model_info():
    luna = get_model_info("openai/gpt-6-luna")
    assert luna["name"] == "GPT-6 Luna (medium)"
    assert luna["input"] == 0.10
    assert luna["output"] == 0.50

    flash = get_model_info("gemini-3.8-flash")
    assert flash["intelligence_index"] == 40

    unknown = get_model_info("non-existent-model")
    assert unknown["name"] == "non-existent-model"

def test_estimate_cost():
    # 1 milhão de tokens de entrada + 1 milhão de tokens de saída no GPT-6 Luna
    # 0.10 + 0.50 = 0.60 USD
    cost = estimate_cost("luna", tokens_in=1_000_000, tokens_out=1_000_000)
    assert cost == 0.60

    # 10.000 tokens in, 2.000 tokens out no Luna:
    # (10000/1M * 0.10) + (2000/1M * 0.50) = 0.001 + 0.001 = 0.002 USD
    cost_small = estimate_cost("luna", tokens_in=10_000, tokens_out=2_000)
    assert cost_small == 0.002
