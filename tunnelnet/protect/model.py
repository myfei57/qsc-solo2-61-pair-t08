"""Latched protection incidents, one per cable."""

from __future__ import annotations

from dataclasses import dataclass

NORMAL = "normal"
TRIPPED = "tripped"
DEGRADED = "degraded"
RESETTABLE = "resettable"


@dataclass
class Incident:
    """One latched over temperature event.

    The pre-action actuator states are snapshotted at trip time so a manual
    reset restores exactly what the protection changed, no more.  The two
    ``*_secured`` flags confirm each safe-state step; while any of them is
    False the incident stays ``degraded`` and the step is retried on every
    evaluation until the safe position is reached.
    """

    cable_id: str
    cabin: str
    state: str
    tripped_at: str
    hold: int = 0
    fan_was_running: bool = True
    door_was_open: bool = False
    valve_was_open: bool = False
    fan_secured: bool = False
    isolation_secured: bool = False
