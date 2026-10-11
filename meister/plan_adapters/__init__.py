"""meister.plan_adapters — Package init; auto-registers all bundled adapters."""

from meister.plan_adapters import superpowers  # noqa: F401 — side-effect: registers adapter
from meister.plan_adapters import markdown  # noqa: F401 — side-effect: registers adapter

__all__ = ["markdown", "superpowers"]
