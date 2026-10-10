"""Operações de processo específicas do sistema operacional.

A implementação é escolhida por ``sys.platform``; quem chama importa apenas
as funções públicas deste pacote.
"""

from __future__ import annotations

import sys

if sys.platform == "win32":
    from meister.osops import windows as _impl
else:
    from meister.osops import posix as _impl

ProcessInspectionError = _impl.ProcessInspectionError
pid_alive = _impl.pid_alive
process_signature = _impl.process_signature
popen_session_kwargs = _impl.popen_session_kwargs
signal_group = _impl.signal_group
interrupt_group = _impl.interrupt_group
terminate_tree = _impl.terminate_tree
kill_tree = _impl.kill_tree
lock_file = _impl.lock_file
unlock_file = _impl.unlock_file
list_processes = _impl.list_processes
kill_self = _impl.kill_self

__all__ = [
    "ProcessInspectionError",
    "interrupt_group",
    "kill_self",
    "kill_tree",
    "list_processes",
    "lock_file",
    "pid_alive",
    "popen_session_kwargs",
    "process_signature",
    "signal_group",
    "terminate_tree",
    "unlock_file",
]
