"""Compatibilidade: movido para meister.engine.dag."""

import sys
from typing import TYPE_CHECKING

from meister.engine import dag as _module

if TYPE_CHECKING:  # o mypy não acompanha a troca em sys.modules
    from meister.engine.dag import *  # noqa: F403

sys.modules[__name__] = _module
