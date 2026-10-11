"""meister.plan_adapters.markdown — the native Markdown task format.

Same converter as the "superpowers" adapter (same format); registered as
"markdown" so the native name works without depending on the skill.
"""

from meister.plan import register_adapter
from meister.plan_adapters.superpowers import convert

register_adapter("markdown")(convert)

__all__ = ["convert"]
