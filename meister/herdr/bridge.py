"""Compatibilidade: movido para meister.engine.bridge."""

import sys
from typing import TYPE_CHECKING

from meister.engine import bridge as _module

if TYPE_CHECKING:  # o mypy não acompanha a troca em sys.modules
    from meister.engine.bridge import *  # noqa: F403

# O código real vive em meister/engine/bridge.py, onde ficam, por exemplo,
# 'run_id: str = str(run_record["run_id"])' e "sm.transition_run(run_id, to_state=RunState.RUNNING)".
sys.modules[__name__] = _module
