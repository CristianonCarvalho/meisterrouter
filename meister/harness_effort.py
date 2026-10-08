"""Supported effort values for worker harnesses."""

from __future__ import annotations

from typing import Dict, Tuple


HARNESS_EFFORT_VALUES: Dict[str, Tuple[str, ...]] = {
    "copilot": ("none", "minimal", "low", "medium", "high", "xhigh", "max"),
    "github-copilot": ("none", "minimal", "low", "medium", "high", "xhigh", "max"),
    "claude": ("low", "medium", "high", "xhigh", "max"),
    "agy": ("low", "medium", "high", "xhigh", "max"),
    "antigravity": ("low", "medium", "high", "xhigh", "max"),
    "codex": ("minimal", "low", "medium", "high", "xhigh"),
}
