"""Contrato de hosts de workers do MeisterRouter."""

from __future__ import annotations

from meister.hosts.base import (
    CAP_PUSH_EVENTS,
    CAP_VISIBLE,
    EventCallback,
    HostError,
    WorkerCommand,
    WorkerHandle,
    WorkerHost,
)
from meister.hosts.herdr import HerdrHost
from meister.hosts.process import ProcessHost
from meister.hosts.select import describe_host, select_host
from meister.hosts.tmux import TmuxHost

__all__ = [
    "CAP_PUSH_EVENTS",
    "CAP_VISIBLE",
    "EventCallback",
    "HerdrHost",
    "HostError",
    "ProcessHost",
    "TmuxHost",
    "WorkerCommand",
    "WorkerHandle",
    "WorkerHost",
    "describe_host",
    "select_host",
]
