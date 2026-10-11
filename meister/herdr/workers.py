"""Compatibilidade: movido para meister.engine.workers."""

import sys
from typing import TYPE_CHECKING

from meister.engine import workers as _module

if TYPE_CHECKING:  # o mypy não acompanha a troca em sys.modules
    from meister.engine.workers import *  # noqa: F403

sys.modules[__name__] = _module
