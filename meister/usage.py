"""Parsing and catalog estimation for local worker usage."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, replace
from typing import Any, Optional


@dataclass
class WorkerUsage:
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    tokens_total: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    thinking_tokens: Optional[int] = None
    credits: Optional[float] = None
    cost_usd: Optional[float] = None
    cost_source: str = "unknown"
    approx: bool = False

    def to_dict(self) -> dict[str, Any]:
        result = {
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_total": self.tokens_total,
            "cache_read_tokens": self.cache_read_tokens,
            "thinking_tokens": self.thinking_tokens,
            "credits": self.credits,
            "cost": self.cost_usd,
            "cost_source": self.cost_source,
            "approx": self.approx,
        }
        return {key: value for key, value in result.items() if value is not None}


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _integer(value: Any) -> Optional[int]:
    number = _number(value)
    if number is None or not number.is_integer() or number < 0:
        return None
    return int(number)


def _json_object(raw: str) -> Optional[dict[str, Any]]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidates = []
    lines = raw.splitlines(keepends=True)
    offset = 0
    for line in lines:
        if line.lstrip().startswith("{"):
            candidates.append(offset + len(line) - len(line.lstrip()))
        offset += len(line)
    for start in reversed(candidates):
        try:
            obj, _ = json.JSONDecoder().raw_decode(raw[start:])
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def parse_claude_json(raw: str) -> tuple[str, WorkerUsage]:
    obj = _json_object(raw)
    if obj is None or not isinstance(obj.get("result"), str):
        return raw, WorkerUsage()
    usage_data = obj.get("usage")
    usage_data = usage_data if isinstance(usage_data, dict) else {}
    input_tokens = _integer(usage_data.get("input_tokens"))
    cache_creation = _integer(usage_data.get("cache_creation_input_tokens"))
    cache_read = _integer(usage_data.get("cache_read_input_tokens"))
    tokens_in = (
        input_tokens + (cache_creation or 0) + (cache_read or 0)
        if input_tokens is not None
        else None
    )
    cost = _number(obj.get("total_cost_usd"))
    known = tokens_in is not None or _integer(usage_data.get("output_tokens")) is not None or cost is not None
    return obj["result"], WorkerUsage(
        tokens_in=tokens_in,
        tokens_out=_integer(usage_data.get("output_tokens")),
        cache_read_tokens=cache_read,
        cost_usd=cost,
        cost_source="reported" if cost is not None else "unknown",
    ) if known else WorkerUsage()


def parse_agy_json(raw: str) -> tuple[str, WorkerUsage]:
    obj = _json_object(raw)
    if obj is None or not isinstance(obj.get("response"), str):
        return raw, WorkerUsage()
    usage_data = obj.get("usage")
    if not isinstance(usage_data, dict):
        return obj["response"], WorkerUsage()
    return obj["response"], WorkerUsage(
        tokens_in=_integer(usage_data.get("input_tokens")),
        tokens_out=_integer(usage_data.get("output_tokens")),
        tokens_total=_integer(usage_data.get("total_tokens")),
        cache_read_tokens=_integer(usage_data.get("cache_read_tokens")),
        thinking_tokens=_integer(usage_data.get("thinking_tokens")),
    )


def parse_codex_text(raw: str) -> tuple[str, WorkerUsage]:
    matches = re.findall(r"(?im)^\s*tokens\s+used\s*\r?\n\s*([\d,]+)\s*$", raw)
    if not matches:
        return raw, WorkerUsage()
    try:
        tokens_total = int(matches[-1].replace(",", ""))
    except (ValueError, TypeError):
        return raw, WorkerUsage()
    return raw, WorkerUsage(tokens_total=tokens_total)


_SUFFIX = {"k": 1_000, "m": 1_000_000}


def _parse_token_count(value: str) -> Optional[int]:
    text = value.strip().replace(",", "").lower()
    factor = 1
    if text and text[-1] in _SUFFIX:
        factor = _SUFFIX[text[-1]]
        text = text[:-1]
    try:
        number = float(text) * factor
    except (ValueError, TypeError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return int(round(number))


_COPILOT_TOKENS = re.compile(
    r"^[ \t]*Tokens[ \t]+↑[ \t]*([\d,.]+[km]?)(?:[ \t]*\(([^)\n]*)\))?"
    r"[ \t]*•[ \t]*↓[ \t]*([\d,.]+[km]?)(?:[ \t]*\(([^)\n]*)\))?",
    re.IGNORECASE | re.MULTILINE,
)
_COPILOT_PART = re.compile(r"([\d,.]+[km]?)[ \t]+(cached|written|reasoning)", re.IGNORECASE)


def _copilot_parts(detail: Optional[str]) -> dict[str, Optional[int]]:
    """Itens entre parênteses, ex.: `7.1m cached, 149.7k written` ou `35 reasoning`."""
    parts: dict[str, Optional[int]] = {}
    for amount, name in _COPILOT_PART.findall(detail or ""):
        parts[name.lower()] = _parse_token_count(amount)
    return parts


def parse_copilot_text(raw: str) -> tuple[str, WorkerUsage]:
    credits_matches = re.findall(r"(?im)^\s*AI Credits\s+(\d+(?:\.\d+)?)", raw)
    token_matches = _COPILOT_TOKENS.findall(raw)
    try:
        credits = float(credits_matches[-1]) if credits_matches else None
    except (ValueError, TypeError):
        credits = None
    tokens_in = tokens_out = cache_read = thinking = None
    approx = False
    if token_matches:
        in_raw, in_detail, out_raw, out_detail = token_matches[-1]
        tokens_in = _parse_token_count(in_raw)
        tokens_out = _parse_token_count(out_raw)
        cache_read = _copilot_parts(in_detail).get("cached")
        thinking = _copilot_parts(out_detail).get("reasoning")
        approx = any(value.strip().lower()[-1:] in _SUFFIX for value in (in_raw, out_raw))
    if credits is None and tokens_in is None and tokens_out is None:
        return raw, WorkerUsage()
    return raw, WorkerUsage(
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cache_read_tokens=cache_read,
        thinking_tokens=thinking,
        credits=credits,
        approx=approx,
    )


def parse_for_harness(harness: str, raw: str) -> tuple[str, WorkerUsage]:
    if harness == "claude":
        return parse_claude_json(raw)
    if harness in ("agy", "antigravity"):
        return parse_agy_json(raw)
    if harness == "codex":
        return parse_codex_text(raw)
    if harness == "copilot":
        return parse_copilot_text(raw)
    return raw, WorkerUsage()


def catalog_cost(
    key: str,
    tokens_in: Optional[int],
    tokens_out: Optional[int],
    config: Optional[Any] = None,
) -> Optional[float]:
    if tokens_in is None and tokens_out is None:
        return None
    if config is None:
        from meister.config import load_config

        config = load_config()
    tier = _find_tier(key, config)
    if tier is None:
        return None
    total_tokens = (tokens_in or 0) + (tokens_out or 0)
    return round(total_tokens / 1_000_000 * tier.cost_per_m_tokens, 6)


def _find_tier(key: str, config: Optional[Any] = None) -> Optional[Any]:
    if config is None:
        from meister.config import load_config

        config = load_config()
    lookup = key.casefold().strip()
    if not lookup:
        return None
    tiers = [*config.workers.tier_order, *config.workers.disabled]
    tier = next((item for item in tiers if item.name.casefold().strip() == lookup), None)
    if tier is None:
        tier = next((item for item in tiers if item.model.casefold().strip() == lookup), None)
    return tier


def finalize_usage(
    usage: WorkerUsage,
    tier_key: str,
    config: Optional[Any] = None,
) -> WorkerUsage:
    if usage.cost_source == "reported":
        return usage
    if usage.credits is not None:
        tier = _find_tier(tier_key, config)
        if tier is not None and tier.credit_usd is not None:
            return replace(
                usage,
                cost_usd=round(usage.credits * tier.credit_usd, 6),
                cost_source="reported",
            )
    tokens_in = usage.tokens_in
    tokens_out = usage.tokens_out
    if tokens_in is None and tokens_out is None and usage.tokens_total is not None:
        tokens_in = usage.tokens_total
        tokens_out = 0
    cost = catalog_cost(tier_key, tokens_in, tokens_out, config=config)
    if cost is None:
        return replace(usage, cost_usd=None, cost_source="unknown")
    return replace(usage, cost_usd=cost, cost_source="estimated")
