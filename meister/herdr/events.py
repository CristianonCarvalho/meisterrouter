"""Compatibilidade: movido para meister.engine.events."""

import sys
from typing import TYPE_CHECKING

from meister.engine import events as _module

if TYPE_CHECKING:  # o mypy não acompanha a troca em sys.modules
    from meister.engine.events import *  # noqa: F403

sys.modules[__name__] = _module
