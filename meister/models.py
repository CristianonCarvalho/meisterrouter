"""Estimativas de custo baseadas nas vias configuradas."""

import logging
from typing import Optional

from meister.config import MeisterConfig, load_config

logger = logging.getLogger(__name__)


def estimate_cost(
    model_key: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cache_read_tokens: int = 0,
    config: Optional[MeisterConfig] = None,
) -> float:
    """Estima o custo em USD usando o preço único por milhão da via.

    ``cache_read_tokens`` é aceito por compatibilidade, mas ignorado porque o
    catálogo configura um único preço por milhão de tokens.
    """
    del cache_read_tokens
    config = config or load_config()
    key = model_key.casefold().strip()
    if not key:
        logger.debug("Não há chave de via para estimar o custo")
        return 0.0

    tiers = [*config.workers.tier_order, *config.workers.disabled]
    for tier in tiers:
        if tier.name.casefold().strip() == key:
            return round(
                (tokens_in + tokens_out) / 1_000_000 * tier.cost_per_m_tokens,
                6,
            )

    for tier in tiers:
        if tier.model.casefold().strip() == key:
            return round(
                (tokens_in + tokens_out) / 1_000_000 * tier.cost_per_m_tokens,
                6,
            )

    logger.debug("Chave de via desconhecida ao estimar custo: %s", model_key)
    return 0.0
