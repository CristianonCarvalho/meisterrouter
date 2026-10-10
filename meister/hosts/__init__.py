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

__all__ = [
    "CAP_PUSH_EVENTS",
    "CAP_VISIBLE",
    "EventCallback",
    "HerdrHost",
    "HostError",
    "WorkerCommand",
    "WorkerHandle",
    "WorkerHost",
]
